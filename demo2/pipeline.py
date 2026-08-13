"""Demo2 candidate main line: Tasks Library + Stage 1 + 1.0 direct MuJoCo.

The generated artifact is still ``driver.py`` and the model still receives the
AutoAdapter 1.0 study/generate tool surface.  Stage 1 is the sole authority for
which public effects are generated.  The isolated Blue Line compiles one test
for each covered Tasks Library requirement.  Validation B is not used here.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERAL_SRC = REPO_ROOT / "general_demo" / "src"
LEGACY_CORE = REPO_ROOT / "demo2" / "legacy_core"
LEGACY_RUNTIME = REPO_ROOT / "demo2" / "legacy_runtime"
for _path in (GENERAL_SRC, LEGACY_RUNTIME, LEGACY_CORE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from autoadapter2.generation import FixtureJsonGenerator, Stage1Config, Stage1Runner
from autoadapter2.libraries import TaskLibraryPackage, TasksLibrary
from auto_adapter.orchestrator import SelfAssembleConfig

from .blue_line import TaskBlueLineRunner, requirement_blue_body
from .legacy_generation import Stage1BoundSelfAssemble
from .model import (
    BedrockJsonGenerator,
    OpenAICompatibleConfig,
    OpenAIJsonGenerator,
)
from .stage1_scope import (
    DecisionScope,
    PolicyAwareGenerator,
    decision_scope,
    public_projection,
    reference_design_body,
)


G2_PROFILE = {
    "profile_id": "g2-reusable-effect",
    "version": "1.0.0",
    "granularity": "G2",
}

# Runnable experiment configurations with complete, local MuJoCo closures.
# SO follower is intentionally excluded from this experiment at the user's
# request; keeping its imported source assets does not admit it to this list.
SUPPORTED_ROBOTS = (
    "robotstudio_so101",
    "franka_panda",
    "unitree-go2",
    "kuka_iiwa_14",
    "piper",
    "universal_robots_ur5e",
    "pushbench",
)
REFERENCE_STUDIES = {
    "robotstudio_so101": "so101/study.json",
    "franka_panda": "franka/study.json",
    "unitree-go2": "go2/study.json",
    "kuka_iiwa_14": "kuka/study.json",
    "piper": "piper/study.json",
    "universal_robots_ur5e": "ur5e/study.json",
    "pushbench": "pushbench/study.json",
}


class Demo2Error(RuntimeError):
    """One bounded Demo2 run could not continue."""


@dataclass(frozen=True)
class ModelSettings:
    provider: str = "bedrock"
    model: str = "us.anthropic.claude-sonnet-4-6"
    region: str = "us-east-1"
    base_url: str | None = None
    endpoint_path: str = "/v1/chat/completions"
    api_key_env: str = "AUTOADAPTER_MODEL_API_KEY"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    thinking: str | None = None
    max_tokens: int = 8000


@dataclass(frozen=True)
class RobotPackage:
    robot_id: str
    morphology: dict[str, Any]
    tasks: TaskLibraryPackage
    private_evaluation: dict[str, Any]
    task_instances: dict[str, Any]
    direct_adapter: dict[str, Any]
    stage1_policy: dict[str, Any]
    experience: dict[str, Any]
    task_directory: Path
    reference_driver_path: Path
    reference_study_path: Path
    mjcf_path: Path

    @property
    def robot_configuration_id(self) -> str:
        return str(self.morphology["robot_configuration_id"])

    @property
    def catalog_version(self) -> str:
        return str(self.morphology["tasks"]["catalog_version"])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Demo2Error(f"required Demo2 input is missing: {path}") from exc
    if not isinstance(value, dict):
        raise Demo2Error(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resolve_package(robot_id: str) -> RobotPackage:
    """Resolve only Demo2-owned data and a complete local MuJoCo closure."""

    if robot_id not in SUPPORTED_ROBOTS:
        raise Demo2Error(f"unknown runnable robot {robot_id!r}; choose one of {SUPPORTED_ROBOTS}")
    libraries = REPO_ROOT / "demo2" / "libraries"
    morphology_path = libraries / "morphology" / robot_id / "1.0.0" / "record.json"
    morphology = _read_json(morphology_path)
    tasks_ref = morphology.get("tasks")
    if not isinstance(tasks_ref, Mapping):
        raise Demo2Error(f"{morphology_path} must select one Tasks Library package")
    configuration = str(tasks_ref.get("robot_configuration_id", ""))
    version = str(tasks_ref.get("catalog_version", ""))
    try:
        task_package = TasksLibrary(libraries / "tasks").load(configuration, version)
    except Exception as exc:
        raise Demo2Error(f"Tasks Library could not load {configuration}@{version}: {exc}") from exc

    task_directory = libraries / "tasks" / configuration / version
    private_evaluation = _read_json(task_directory / "evaluation_private.json")
    task_instances = _read_json(task_directory / "task_instances_private.json")
    direct_adapter = _read_json(task_directory / "direct_mujoco_adapter.json")
    stage1_policy = _read_json(task_directory / "stage1_capability_policy.json")
    identities = {
        private_evaluation.get("robot_configuration_id"),
        task_instances.get("robot_configuration_id"),
        direct_adapter.get("robot_configuration_id"),
        stage1_policy.get("robot_configuration_id"),
        task_package.robot_configuration_id,
    }
    if identities != {configuration}:
        raise Demo2Error(f"Demo2 package identity mismatch for {robot_id}: {sorted(map(str, identities))}")

    experience_path = libraries / "experience" / robot_id / "1.0.0" / "records.json"
    reference_driver = REPO_ROOT / str(morphology["driver"]["source"])
    reference_study = REPO_ROOT / "demo2" / "legacy_references" / REFERENCE_STUDIES[robot_id]
    mjcf = REPO_ROOT / "demo2" / "legacy_assets" / str(morphology["mujoco"]["entrypoint"])
    for required in (experience_path, reference_driver, reference_study, mjcf):
        if not required.is_file():
            raise Demo2Error(f"required Demo2 source is missing: {required}")
    return RobotPackage(
        robot_id=robot_id,
        morphology=morphology,
        tasks=task_package,
        private_evaluation=private_evaluation,
        task_instances=task_instances,
        direct_adapter=direct_adapter,
        stage1_policy=stage1_policy,
        experience=_read_json(experience_path),
        task_directory=task_directory,
        reference_driver_path=reference_driver,
        reference_study_path=reference_study,
        mjcf_path=mjcf,
    )


def _run_inputs(package: RobotPackage, run_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    stage1_tasks = package.tasks.stage1_projection(run_id)
    public_tasks = package.tasks.demo_public_tasks(run_id)
    if any(set(item) != {"requirement_id", "description"} for item in stage1_tasks):
        raise Demo2Error("Tasks Library leaked fields into the formal Stage 1 task projection")
    projection = public_projection(package.morphology, package.stage1_policy, public_tasks)
    return stage1_tasks, public_tasks, projection


def inspect_package(robot_id: str) -> dict[str, Any]:
    package = resolve_package(robot_id)
    _, public_tasks, projection = _run_inputs(package, f"demo2-inspect-{robot_id}")
    return {
        "robot_id": robot_id,
        "robot_model_id": package.morphology["robot_model_id"],
        "robot_configuration_id": package.robot_configuration_id,
        "capability_effects": list(projection["effect_allowlist"]),
        "public_tasks": copy.deepcopy(public_tasks),
        "complete_mjcf": str(package.mjcf_path.resolve()),
        "skeleton_family": package.morphology["driver"]["family"],
        "reference_driver": str(package.reference_driver_path.resolve()),
        "experience_count": len(package.experience.get("records", [])),
        "tasks_library_root": str((REPO_ROOT / "demo2" / "libraries" / "tasks").resolve()),
    }


def _openai_config(settings: ModelSettings) -> OpenAICompatibleConfig:
    if not settings.base_url:
        raise Demo2Error("OpenAI-compatible provider requires --base-url")
    api_key = os.environ.get(settings.api_key_env, "").strip()
    if not api_key:
        raise Demo2Error(f"missing model credential environment variable {settings.api_key_env}")
    return OpenAICompatibleConfig(
        api_key=api_key,
        base_url=settings.base_url,
        model=settings.model,
        endpoint_path=settings.endpoint_path,
        auth_header=settings.auth_header,
        auth_prefix=settings.auth_prefix,
        thinking=settings.thinking,
    )


def _json_generator(settings: ModelSettings):
    if settings.provider == "bedrock":
        return BedrockJsonGenerator(
            model=settings.model,
            region=settings.region,
            max_tokens=settings.max_tokens,
        )
    if settings.provider == "openai":
        return OpenAIJsonGenerator(_openai_config(settings), max_tokens=settings.max_tokens)
    raise Demo2Error(f"unknown model provider {settings.provider!r}")


def _stage1(
    package: RobotPackage,
    run_id: str,
    stage1_tasks: list[dict[str, str]],
    public_tasks: list[dict[str, str]],
    projection: Mapping[str, Any],
    generator: Any,
    *,
    reference: bool,
):
    if reference:
        body = reference_design_body(projection, stage1_tasks)
        actual_generator = FixtureJsonGenerator([body])
    else:
        actual_generator = PolicyAwareGenerator(generator)
    result = Stage1Runner(actual_generator, Stage1Config(max_correction_calls=2)).run(
        run_id,
        projection,
        stage1_tasks,
        G2_PROFILE,
    )
    if result.status != "SEALED" or result.capability_design is None:
        raise Demo2Error(f"Stage 1 failed: {result.diagnostics}")
    scope = decision_scope(result.capability_design, public_tasks, package.stage1_policy)
    return result, scope


def _blue_line(
    package: RobotPackage,
    stage1: Any,
    scope: DecisionScope,
    public_tasks: list[dict[str, str]],
    generator: Any,
    *,
    reference: bool,
):
    if reference:
        expected = requirement_blue_body(
            stage1.capability_design,
            scope,
            public_tasks,
            package.tasks.demo_private_criteria(),
            package.task_instances,
            package.direct_adapter,
            package.private_evaluation["common_guards"],
        )
        actual_generator = FixtureJsonGenerator([expected])
    else:
        actual_generator = generator
    result = TaskBlueLineRunner(actual_generator).run(
        stage1.capability_design,
        stage1.design_hash,
        scope,
        public_tasks,
        package.private_evaluation,
        package.task_instances,
        package.direct_adapter,
    )
    if result.status != "READY" or result.suite is None:
        raise Demo2Error(f"Blue Line failed: {result.diagnostics}")
    return result


def _phase_record(phase: Any) -> dict[str, Any]:
    return {
        "name": phase.name,
        "ok": phase.ok,
        "duration_sec": phase.duration_sec,
        "trace_path": str(phase.trace_path) if phase.trace_path else None,
        "artifacts": [str(path) for path in phase.artifact_paths],
        "error": phase.error,
        "token_usage": phase.token_usage,
    }


def _trace_probe(workspace: Path, attempts: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for attempt in range(1, attempts + 1):
        trace = workspace / "traces" / f"02_generate_attempt_{attempt}.jsonl"
        if not trace.is_file():
            continue
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            for action in step.get("actions", []):
                name = action.get("name")
                if isinstance(name, str):
                    counts[name] = counts.get(name, 0) + 1
    return {
        "mode": "dynamic_1_0_self_assemble",
        "complete_mjcf_visible": True,
        "sealed_design_visible": True,
        "blue_line_hidden_before_initial_generation": True,
        "tool_action_counts": counts,
        "skeleton_inspected": counts.get("inspect_skeleton", 0) > 0,
        "direct_mujoco_probe_attempted": counts.get("local_exec", 0) > 0,
    }


def _report_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("requirement_results", "tests"):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping) and row.get("requirement_id")]
    return []


def _evolution(package: RobotPackage, report: Mapping[str, Any], scope: DecisionScope) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for row in _report_rows(report):
        if row.get("ok"):
            continue
        failures.append({
            "requirement_id": row.get("requirement_id"),
            "task_id": row.get("task_id"),
            "capability_id": row.get("capability_id"),
            "effect": row.get("effect", row.get("method")),
            "detail": row.get("detail"),
            "criteria": copy.deepcopy(row.get("criteria", [])),
        })
    return {
        "artifact_type": "demo2_evolution_sidecar",
        "schema_version": "1.0.0",
        "status": "PROPOSED_REVIEW_REQUIRED",
        "main_run_result_unchanged": True,
        "robot_model_id": package.morphology["robot_model_id"],
        "selected_capability_ids": list(scope.selected_capability_ids),
        "capability_effect_scope": list(scope.selected_effects),
        "covered_requirement_ids": list(scope.covered_requirement_ids),
        "observed_failures": failures,
        "proposal": (
            "Retain the selected 1.0 skeleton/Spec mapping for a later run."
            if not failures else
            "Feed complete public actual values, standards and exceptions into the next 1.0 GENERATE pass."
        ),
    }


def _summary(
    package: RobotPackage,
    report: Mapping[str, Any],
    scope: DecisionScope,
    *,
    mode: str,
    model: str | None,
    attempts: int,
) -> dict[str, Any]:
    rows = _report_rows(report)
    expected = list(scope.covered_requirement_ids)
    observed = [str(row["requirement_id"]) for row in rows]
    exact_report_coverage = len(observed) == len(set(observed)) and set(observed) == set(expected)
    passed_requirements = sorted(
        str(row["requirement_id"]) for row in rows if row.get("ok") is True
    )
    failed_requirements = sorted(set(expected) - set(passed_requirements))
    selected_validation_passed = bool(report.get("all_ok")) and exact_report_coverage
    full_robot_passed = selected_validation_passed and scope.full_task_coverage
    criteria = [
        criterion
        for row in rows
        for criterion in row.get("criteria", [])
        if isinstance(criterion, Mapping)
    ]
    return {
        "artifact_type": "demo2_run_summary",
        "schema_version": "2.0.0",
        "robot_id": package.robot_id,
        "robot_configuration_id": package.robot_configuration_id,
        "run_status": "RUN_COMPLETED",
        "pipeline_completed": True,
        "physical_validation_executed": bool(report.get("physical_validation_executed", rows)),
        "full_task_coverage": scope.full_task_coverage,
        "selected_validation_passed": selected_validation_passed,
        "all_tasks_passed": full_robot_passed,
        "validation_passed": full_robot_passed,
        "structural_ok": bool(report.get("structural_ok")),
        "selected_capability_ids": list(scope.selected_capability_ids),
        "selected_effects": list(scope.selected_effects),
        "covered_requirement_ids": expected,
        "unsupported_requirement_ids": list(scope.unsupported_requirement_ids),
        "blocking_requirement_ids": list(scope.blocking_requirement_ids),
        "passed_requirement_ids": passed_requirements,
        "failed_requirement_ids": failed_requirements,
        "requirements_passed": len(passed_requirements),
        "requirements_total": len(expected),
        "criteria_passed": sum(1 for item in criteria if item.get("ok") is True),
        "criteria_total": len(criteria),
        "exact_report_coverage": exact_report_coverage,
        "generation_mode": mode,
        "model": model,
        "generation_attempts": attempts,
        "validator": "autoadapter_1_direct_mujoco_tasks",
        "validation_b_used": False,
        "recording": report.get("recording"),
        "video_error": report.get("video_error"),
    }


def _prepare_workspace(output_root: str | Path, robot_id: str) -> Path:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / robot_id
    if workspace.exists():
        raise Demo2Error(f"run workspace already exists: {workspace}")
    workspace.mkdir()
    return workspace


def _task_validator():
    # Imported lazily so the task-library/Stage-1 boundary remains inspectable
    # even when MuJoCo is not installed in the caller's Python environment.
    try:
        from .task_validation import task_repair_feedback, validate_task_driver
    except (ImportError, ModuleNotFoundError) as exc:
        raise Demo2Error(f"Demo2 task validator is unavailable: {exc}") from exc
    return validate_task_driver, task_repair_feedback


def run_robot(
    robot_id: str,
    output_root: str | Path,
    *,
    settings: ModelSettings = ModelSettings(),
    reference_calibration: bool = False,
    record_video: bool = True,
    max_generation_attempts: int = 3,
) -> dict[str, Any]:
    """Run one robot once; criterion failures are results, not pipeline blockers."""

    package = resolve_package(robot_id)
    workspace = _prepare_workspace(output_root, robot_id)
    run_id = f"demo2-{robot_id}-{workspace.parent.name}"
    generator = None if reference_calibration else _json_generator(settings)
    validate_task_driver, task_repair_feedback = _task_validator()
    try:
        stage1_tasks, public_tasks, projection = _run_inputs(package, run_id)
        stage1, scope = _stage1(
            package,
            run_id,
            stage1_tasks,
            public_tasks,
            projection,
            generator,
            reference=reference_calibration,
        )
        _write_json(workspace / "sealed_design.json", stage1.capability_design)
        _write_json(workspace / "decision_scope.json", scope.to_dict())
        _write_json(workspace / "stage1.json", {
            "status": stage1.status,
            "mode": "reference_task_baseline" if reference_calibration else "dynamic_model",
            "tasks_library_source": "demo2/libraries/tasks",
            "formal_task_projection": stage1_tasks,
            "design": stage1.capability_design,
            "design_hash": stage1.design_hash,
            "seal": stage1.seal,
            "calls": list(stage1.call_log),
            "scope": scope.to_dict(),
        })
        blue = _blue_line(
            package,
            stage1,
            scope,
            public_tasks,
            generator,
            reference=reference_calibration,
        )

        generation_phases: list[dict[str, Any]] = []
        attempts = 1
        if reference_calibration:
            shutil.copy2(package.reference_driver_path, workspace / "driver.py")
            shutil.copy2(package.reference_study_path, workspace / "study.json")
            (workspace / "mjcf.xml").symlink_to(package.mjcf_path.resolve())
            _write_json(workspace / "generation.json", {
                "mode": "reference_task_baseline",
                "formal_generation": False,
                "source": str(package.reference_driver_path),
                "limitations": "This calibrates the official task path; it is not model synthesis evidence.",
            })
            _write_json(workspace / "generation_probe.json", {
                "mode": "reference_task_baseline",
                "complete_mjcf_visible": True,
                "direct_mujoco_probe_attempted": False,
            })
            _write_json(workspace / "blue_line.json", {
                "status": blue.status,
                "mode": "reference_fixture_compiled_from_private_tasks",
                "suite": blue.suite,
                "calls": list(blue.calls),
            })
            report = validate_task_driver(
                workspace,
                blue.suite,
                asset_root=REPO_ROOT / "demo2" / "legacy_assets",
                expected_requirement_ids=scope.covered_requirement_ids,
                expected_capability_ids=scope.selected_capability_ids,
                record_video=record_video,
            )
        else:
            openai = _openai_config(settings) if settings.provider == "openai" else None
            config = SelfAssembleConfig(
                robot_id=robot_id,
                mjcf_path=package.mjcf_path,
                workspace_root=workspace.parent,
                mode="local",
                validate_mode="framework",
                aws_region=settings.region,
                bedrock_model=settings.model,
                max_outer_gen_val_iters=max_generation_attempts,
                max_tokens_per_turn=settings.max_tokens,
            )
            flow = Stage1BoundSelfAssemble(config, openai_config=openai)
            report: dict[str, Any] | None = None
            feedback: str | None = None
            with flow:
                study = flow._phase_study()
                generation_phases.append(_phase_record(study))
                if not study.ok:
                    raise Demo2Error(f"1.0 STUDY failed: {study.error}")
                for attempts in range(1, max_generation_attempts + 1):
                    generated = flow._phase_generate(feedback)
                    generation_phases.append(_phase_record(generated))
                    current_trace = workspace / "traces" / "02_generate.jsonl"
                    if current_trace.is_file():
                        shutil.copy2(current_trace, workspace / "traces" / f"02_generate_attempt_{attempts}.jsonl")
                    if not generated.ok:
                        raise Demo2Error(f"1.0 GENERATE failed: {generated.error}")
                    if attempts == 1:
                        # The initial generation is complete before the private suite is persisted.
                        _write_json(workspace / "blue_line.json", {
                            "status": blue.status,
                            "mode": "dynamic_isolated_model",
                            "suite": blue.suite,
                            "calls": list(blue.calls),
                        })
                    report = validate_task_driver(
                        workspace,
                        blue.suite,
                        asset_root=REPO_ROOT / "demo2" / "legacy_assets",
                        expected_requirement_ids=scope.covered_requirement_ids,
                        expected_capability_ids=scope.selected_capability_ids,
                        record_video=record_video,
                    )
                    _write_json(workspace / f"validate_report_attempt_{attempts}.json", report)
                    if report.get("all_ok"):
                        break
                    feedback = task_repair_feedback(report)
            assert report is not None
            _write_json(workspace / "generation.json", {
                "mode": "dynamic_1_0_self_assemble",
                "model": settings.model,
                "provider": settings.provider,
                "attempts": attempts,
                "phases": generation_phases,
            })
            _write_json(workspace / "generation_probe.json", _trace_probe(workspace, attempts))

        summary = _summary(
            package,
            report,
            scope,
            mode="reference_task_baseline" if reference_calibration else "dynamic_1_0_self_assemble",
            model=None if reference_calibration else settings.model,
            attempts=attempts,
        )
        _write_json(workspace / "summary.json", summary)
        _write_json(workspace / "evolution.json", _evolution(package, report, scope))
        return summary
    except Exception as exc:
        _write_json(workspace / "failure.json", {
            "status": "BLOCKED",
            "run_status": "BLOCKED",
            "robot_id": robot_id,
            "robot_configuration_id": package.robot_configuration_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "model": None if reference_calibration else settings.model,
            "validation_b_used": False,
        })
        if isinstance(exc, Demo2Error):
            raise
        raise Demo2Error(f"{type(exc).__name__}: {exc}") from exc


def run_all(
    output_root: str | Path,
    *,
    settings: ModelSettings = ModelSettings(),
    reference_calibration: bool = False,
    record_video: bool = True,
) -> dict[str, Any]:
    """Attempt every registered robot even when an earlier one fails."""

    root = Path(output_root).resolve()
    if root.exists():
        raise Demo2Error(f"output directory already exists: {root}")
    root.mkdir(parents=True)
    results: dict[str, Any] = {}
    for robot_id in SUPPORTED_ROBOTS:
        try:
            results[robot_id] = run_robot(
                robot_id,
                root,
                settings=settings,
                reference_calibration=reference_calibration,
                record_video=record_video,
            )
        except Demo2Error:
            failure = _read_json(root / robot_id / "failure.json")
            results[robot_id] = {
                "robot_id": robot_id,
                "run_status": "BLOCKED",
                "pipeline_completed": False,
                "physical_validation_executed": False,
                "all_tasks_passed": False,
                "validation_passed": False,
                "error": failure["error"],
            }
    completed = [item for item in results.values() if item.get("run_status") == "RUN_COMPLETED"]
    aggregate = {
        "artifact_type": "demo2_multi_robot_summary",
        "schema_version": "2.0.0",
        "all_robots_attempted": len(results) == len(SUPPORTED_ROBOTS),
        "robot_attempt_count": len(results),
        "robot_target_count": len(SUPPORTED_ROBOTS),
        "run_completed_count": len(completed),
        "blocked_count": len(results) - len(completed),
        "fully_passed_count": sum(1 for item in completed if item.get("all_tasks_passed")),
        "partially_or_fully_validated_count": sum(
            1 for item in completed if int(item.get("requirements_passed", 0)) > 0
        ),
        "all_tasks_passed": bool(results) and all(item.get("all_tasks_passed") for item in results.values()),
        "validation_b_used": False,
        "mode": "reference_task_baseline" if reference_calibration else "dynamic_1_0_self_assemble",
        "robots": results,
    }
    _write_json(root / "summary.json", aggregate)
    return aggregate
