"""Demo2: Stage 1 + Blue inputs around the AutoAdapter 1.0 MuJoCo flow."""
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
from auto_adapter.orchestrator import SelfAssembleConfig

from .blue_line import LegacyBlueLineRunner, reference_blue_body
from .legacy_generation import Stage1BoundSelfAssemble
from .legacy_validation import repair_feedback, validate_driver
from .model import (
    BedrockJsonGenerator,
    OpenAICompatibleConfig,
    OpenAIJsonGenerator,
)


G2_PROFILE = {
    "profile_id": "g2-reusable-effect",
    "version": "1.0.0",
    "granularity": "G2",
}
SUPPORTED_ROBOTS = ("robotstudio_so101", "franka_panda", "unitree-go2")
REFERENCE_STUDIES = {
    "robotstudio_so101": "so101/study.json",
    "franka_panda": "franka/study.json",
    "unitree-go2": "go2/study.json",
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
    max_tokens: int = 8000


@dataclass(frozen=True)
class RobotPackage:
    robot_id: str
    morphology: dict[str, Any]
    public_tasks: dict[str, Any]
    private_evaluation: dict[str, Any]
    experience: dict[str, Any]
    morphology_path: Path
    public_tasks_path: Path
    private_evaluation_path: Path
    experience_path: Path
    reference_driver_path: Path
    reference_study_path: Path
    mjcf_path: Path

    @property
    def capability_effects(self) -> tuple[str, ...]:
        return tuple(self.public_tasks["robot_public_projection"]["effect_allowlist"])


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
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
    if robot_id not in SUPPORTED_ROBOTS:
        raise Demo2Error(f"unknown robot {robot_id!r}; choose one of {SUPPORTED_ROBOTS}")
    libraries = REPO_ROOT / "demo2" / "libraries"
    morphology_path = libraries / "morphology" / robot_id / "1.0.0" / "record.json"
    public_path = libraries / "tasks" / robot_id / "1.0.0" / "public_tasks.json"
    private_path = libraries / "tasks" / robot_id / "1.0.0" / "evaluation_private.json"
    experience_path = libraries / "experience" / robot_id / "1.0.0" / "records.json"
    morphology = _read_json(morphology_path)
    reference_driver = REPO_ROOT / morphology["driver"]["source"]
    reference_study = (
        REPO_ROOT / "demo2" / "legacy_references" / REFERENCE_STUDIES[robot_id]
    )
    mjcf = REPO_ROOT / "demo2" / "legacy_assets" / morphology["mujoco"]["entrypoint"]
    for required in (reference_driver, reference_study, mjcf):
        if not required.is_file():
            raise Demo2Error(f"required Demo2 source is missing: {required}")
    return RobotPackage(
        robot_id=robot_id,
        morphology=morphology,
        public_tasks=_read_json(public_path),
        private_evaluation=_read_json(private_path),
        experience=_read_json(experience_path),
        morphology_path=morphology_path,
        public_tasks_path=public_path,
        private_evaluation_path=private_path,
        experience_path=experience_path,
        reference_driver_path=reference_driver,
        reference_study_path=reference_study,
        mjcf_path=mjcf,
    )


def inspect_package(robot_id: str) -> dict[str, Any]:
    package = resolve_package(robot_id)
    return {
        "robot_id": robot_id,
        "robot_model_id": package.morphology["robot_model_id"],
        "robot_configuration_id": package.morphology["robot_configuration_id"],
        "capability_effects": list(package.capability_effects),
        "public_tasks": copy.deepcopy(package.public_tasks["tasks"]),
        "reference_only_tasks": copy.deepcopy(package.public_tasks.get("reference_only_tasks", [])),
        "complete_mjcf": str(package.mjcf_path.resolve()),
        "skeleton_family": package.morphology["driver"]["family"],
        "reference_driver": str(package.reference_driver_path.resolve()),
        "experience_count": len(package.experience.get("records", [])),
    }


def _duration_field() -> dict[str, Any]:
    return {
        "name": "duration", "type": "number", "shape": "scalar",
        "unit": "s", "frame": "none", "required": False,
    }


def _input_fields(effect: str, robot_id: str) -> list[dict[str, Any]]:
    if effect == "move_joints":
        width = 5 if robot_id == "robotstudio_so101" else 7
        return [{
            "name": "target", "type": "number", "shape": f"vector:{width}",
            "unit": "rad", "frame": "joint", "required": True,
        }, _duration_field()]
    if effect == "move_to_cartesian":
        return [{
            "name": "position", "type": "number", "shape": "vector:3",
            "unit": "m", "frame": "robot_base", "required": True,
        }, _duration_field()]
    if effect in {"stand_up", "sit"}:
        return [_duration_field()]
    raise Demo2Error(f"no MVP input mapping for {effect}")


def _reference_design_body(package: RobotPackage) -> dict[str, Any]:
    projection = package.public_tasks["robot_public_projection"]
    action_by_effect = {
        "move_joints": "driver joint-space motion",
        "move_to_cartesian": "driver Cartesian motion",
        "stand_up": "driver posture motion",
        "sit": "driver posture motion",
    }
    observation_by_effect = {
        "move_joints": "joint vector observation",
        "move_to_cartesian": "end-effector pose observation",
        "stand_up": "body pose observation",
        "sit": "body pose observation",
    }
    capabilities: list[dict[str, Any]] = []
    for effect, task in zip(projection["effect_allowlist"], package.public_tasks["tasks"], strict=True):
        capabilities.append({
            "capability_id": effect,
            "kind": "action",
            "requirement_ids": [task["requirement_id"]],
            "inputs": _input_fields(effect, package.robot_id),
            "outputs": [],
            "effect": effect,
            "preconditions": ["robot model is reset"],
            "invocation_semantics": f"Invoke {effect} once with the declared public inputs.",
            "temporal_semantics": "Return after the bounded motion completes.",
            "invariants": ["Report only motion executed by the returned driver object."],
            "required_action_affordances": [action_by_effect[effect]],
            "required_observation_affordances": [observation_by_effect[effect]],
            "errors": [{"code": "MOTION_REJECTED", "message": "The requested motion failed."}],
            "unsupported_scope": [],
        })
    return {
        "capabilities": capabilities,
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
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


def _stage1(package: RobotPackage, generator: Any, *, reference: bool):
    actual_generator = (
        FixtureJsonGenerator([_reference_design_body(package)])
        if reference else generator
    )
    return Stage1Runner(actual_generator, Stage1Config(max_correction_calls=2)).run(
        f"demo2-{package.robot_id}",
        package.public_tasks["robot_public_projection"],
        package.public_tasks["tasks"],
        G2_PROFILE,
    )


def _blue_line(package: RobotPackage, stage1: Any, generator: Any, *, reference: bool):
    actual_generator = generator
    if reference:
        actual_generator = FixtureJsonGenerator([
            reference_blue_body(stage1.capability_design, package.private_evaluation)
        ])
    return LegacyBlueLineRunner(actual_generator).run(
        stage1.capability_design,
        stage1.design_hash,
        package.private_evaluation,
    )


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


def _evolution(
    package: RobotPackage,
    report: Mapping[str, Any],
    selected_effects: tuple[str, ...],
) -> dict[str, Any]:
    failed = [
        {
            "capability_id": test.get("capability_id"),
            "detail": test.get("detail"),
            "actual": test.get("metric"),
            "comparator": test.get("comparator"),
            "threshold": test.get("threshold"),
        }
        for test in report.get("tests", [])
        if test.get("capability_id") and not test.get("ok")
    ]
    return {
        "artifact_type": "demo2_evolution_sidecar",
        "schema_version": "1.0.0",
        "status": "PROPOSED_REVIEW_REQUIRED",
        "main_run_result_unchanged": True,
        "robot_model_id": package.morphology["robot_model_id"],
        "capability_effect_scope": list(selected_effects),
        "observed_failures": failed,
        "proposal": (
            "Retain the selected 1.0 skeleton/Spec mapping for a later run."
            if not failed else
            "Feed the public actual value, standard and exception back into the next 1.0 GENERATE pass."
        ),
    }


def _summary(
    package: RobotPackage,
    report: Mapping[str, Any],
    *,
    mode: str,
    model: str | None,
    attempts: int,
    selected_capability_ids: tuple[str, ...],
) -> dict[str, Any]:
    passed = sorted(
        test["capability_id"]
        for test in report.get("tests", [])
        if test.get("capability_id") and test.get("ok")
    )
    failed = sorted(set(selected_capability_ids) - set(passed))
    return {
        "artifact_type": "demo2_run_summary",
        "schema_version": "1.0.0",
        "robot_id": package.robot_id,
        "pipeline_completed": True,
        "validation_passed": bool(report.get("all_ok")),
        "structural_ok": bool(report.get("structural_ok")),
        "passed_capability_ids": passed,
        "failed_capability_ids": failed,
        "generation_mode": mode,
        "model": model,
        "generation_attempts": attempts,
        "validator": "autoadapter_1_direct_mujoco",
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


def run_robot(
    robot_id: str,
    output_root: str | Path,
    *,
    settings: ModelSettings = ModelSettings(),
    reference_calibration: bool = False,
    record_video: bool = True,
    max_generation_attempts: int = 3,
) -> dict[str, Any]:
    package = resolve_package(robot_id)
    workspace = _prepare_workspace(output_root, robot_id)
    generator = None if reference_calibration else _json_generator(settings)
    try:
        stage1 = _stage1(package, generator, reference=reference_calibration)
        if stage1.status != "SEALED":
            raise Demo2Error(f"Stage 1 failed: {stage1.diagnostics}")
        _write_json(workspace / "sealed_design.json", stage1.capability_design)
        _write_json(workspace / "stage1.json", {
            "status": stage1.status,
            "mode": "reference_calibration" if reference_calibration else "dynamic_model",
            "design": stage1.capability_design,
            "design_hash": stage1.design_hash,
            "seal": stage1.seal,
            "calls": list(stage1.call_log),
        })
        blue = _blue_line(package, stage1, generator, reference=reference_calibration)
        if blue.status != "READY" or blue.suite is None:
            raise Demo2Error(f"Blue Line failed: {blue.diagnostics}")
        selected_effects = tuple(
            capability["effect"] for capability in stage1.capability_design["capabilities"]
        )
        selected_capability_ids = tuple(
            capability["capability_id"]
            for capability in stage1.capability_design["capabilities"]
        )

        generation_phases: list[dict[str, Any]] = []
        attempts = 1
        if reference_calibration:
            shutil.copy2(package.reference_driver_path, workspace / "driver.py")
            shutil.copy2(package.reference_study_path, workspace / "study.json")
            mjcf_link = workspace / "mjcf.xml"
            mjcf_link.symlink_to(package.mjcf_path.resolve())
            _write_json(workspace / "generation.json", {
                "mode": "reference_calibration_only",
                "formal_generation": False,
                "source": str(package.reference_driver_path),
                "limitations": "This proves the physical path, not dynamic synthesis.",
            })
            _write_json(workspace / "generation_probe.json", {
                "mode": "reference_calibration_only",
                "complete_mjcf_visible": True,
                "direct_mujoco_probe_attempted": False,
            })
            _write_json(workspace / "blue_line.json", {
                "status": blue.status,
                "mode": "reference_calibration",
                "suite": blue.suite,
                "calls": list(blue.calls),
            })
            report = validate_driver(workspace, blue.suite, record_video=record_video)
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
                        shutil.copy2(
                            current_trace,
                            workspace / "traces" / f"02_generate_attempt_{attempts}.jsonl",
                        )
                    if not generated.ok:
                        raise Demo2Error(f"1.0 GENERATE failed: {generated.error}")
                    if attempts == 1:
                        _write_json(workspace / "blue_line.json", {
                            "status": blue.status,
                            "mode": "dynamic_isolated_model",
                            "suite": blue.suite,
                            "calls": list(blue.calls),
                        })
                    report = validate_driver(workspace, blue.suite, record_video=record_video)
                    _write_json(
                        workspace / f"validate_report_attempt_{attempts}.json",
                        report,
                    )
                    if report["all_ok"]:
                        break
                    feedback = repair_feedback(report)
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
            mode=(
                "reference_calibration_only"
                if reference_calibration else "dynamic_1_0_self_assemble"
            ),
            model=None if reference_calibration else settings.model,
            attempts=attempts,
            selected_capability_ids=selected_capability_ids,
        )
        _write_json(workspace / "summary.json", summary)
        _write_json(
            workspace / "evolution.json",
            _evolution(package, report, selected_effects),
        )
        return summary
    except Exception as exc:
        _write_json(workspace / "failure.json", {
            "status": "BLOCKED" if "model" in str(exc).lower() else "FAILED",
            "robot_id": robot_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "model": None if reference_calibration else settings.model,
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
    root = Path(output_root).resolve()
    if root.exists():
        raise Demo2Error(f"output directory already exists: {root}")
    root.mkdir(parents=True)
    results: dict[str, Any] = {}
    for robot_id in SUPPORTED_ROBOTS:
        results[robot_id] = run_robot(
            robot_id,
            root,
            settings=settings,
            reference_calibration=reference_calibration,
            record_video=record_video,
        )
    aggregate = {
        "artifact_type": "demo2_three_robot_summary",
        "schema_version": "1.0.0",
        "pipeline_completed": all(item["pipeline_completed"] for item in results.values()),
        "validation_passed": all(item["validation_passed"] for item in results.values()),
        "validation_b_used": False,
        "mode": "reference_calibration_only" if reference_calibration else "dynamic_1_0_self_assemble",
        "robots": results,
    }
    _write_json(root / "summary.json", aggregate)
    return aggregate
