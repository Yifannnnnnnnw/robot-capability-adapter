"""Top-level AutoAdapter 2.0 Direct-MuJoCo experiment orchestration.

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
import random
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoadapter2 import __version__
from autoadapter2.capability_design import (
    run_tgcd,
    validate_capability_design,
    write_capability_design,
)
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
from autoadapter2.evolution import (
    EvolutionError,
    build_experience_review_queue,
    run_evolution,
    validate_experience_snapshot,
)
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
from autoadapter2.task_demo.recap import RecapBudgets, run_recap
from autoadapter2.validation_compiler import (
    IVCError,
    run_ivc,
    validate_capability_validation_suite,
    write_private_suite,
)


DEFAULT_CONDITIONS: tuple[GenerationCondition, ...] = (
    "skeleton-assisted",
    "from-scratch",
)
DEFAULT_CONFIG_PATH = Path("configs/experiments/mainline.json")
_EXPERIENCE_USAGE_SCOPES = frozenset(
    {"next_independent_run_only", "later_matched_run_only"}
)

PHASE_TURN_BUDGETS: Mapping[str, int] = {
    "study": 16,
    "tgcd": 6,
    "ivc": 6,
    "generate_skeleton": 22,
    "generate_from_scratch": 40,
    "repair_skeleton": 22,
    "repair_from_scratch": 20,
}
RECAP_PLANNING_TURNS = 16
RECAP_CAPABILITY_CALLS = 12
TASK_DEMO_TASK_COUNT = 5


class PipelineError(RuntimeError):
    """Raised when the experiment cannot be admitted or composed."""


def _call_supported(function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Pass new stage context through old focused-test seams when possible."""

    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*args, **kwargs)
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return function(*args, **kwargs)
    supported = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return function(*args, **supported)


def _validated_model_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PipelineError("model must be an object")
    required_fields = {
        "vendor",
        "api_protocol",
        "model_id",
        "revision",
        "base_url",
        "context_window_tokens",
        "max_output_tokens",
        "temperature",
        "thinking",
        "tool_history_mode",
        "price_snapshot",
    }
    if set(value) != required_fields:
        missing = sorted(required_fields - set(value))
        extra = sorted(set(value) - required_fields)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise PipelineError("invalid model manifest fields: " + "; ".join(detail))
    for field_name in (
        "vendor",
        "api_protocol",
        "model_id",
        "revision",
        "base_url",
        "tool_history_mode",
    ):
        if not isinstance(value[field_name], str) or not value[field_name].strip():
            raise PipelineError(f"model.{field_name} must be a non-empty string")
    if value["thinking"] is not None and (
        not isinstance(value["thinking"], str) or not value["thinking"].strip()
    ):
        raise PipelineError("model.thinking must be a non-empty string or null")
    if value["api_protocol"] not in {"openai", "openai-compatible"}:
        raise PipelineError("model.api_protocol is unsupported")
    if not str(value["base_url"]).startswith("https://"):
        raise PipelineError("model.base_url must use HTTPS")
    for field_name in ("context_window_tokens", "max_output_tokens"):
        if isinstance(value[field_name], bool) or not isinstance(value[field_name], int):
            raise PipelineError(f"model.{field_name} must be an integer")
        if value[field_name] <= 0:
            raise PipelineError(f"model.{field_name} must be positive")
    if value["max_output_tokens"] > value["context_window_tokens"]:
        raise PipelineError("model.max_output_tokens exceeds its context window")
    temperature = value["temperature"]
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise PipelineError("model.temperature must be numeric")
    if float(temperature) != 0.0:
        raise PipelineError("mainline model.temperature must be 0")
    if value["tool_history_mode"] not in {"native", "text-observation"}:
        raise PipelineError("model.tool_history_mode is unsupported")

    price = value["price_snapshot"]
    required_price_fields = {
        "date",
        "currency",
        "input_per_million_tokens",
        "output_per_million_tokens",
    }
    if not isinstance(price, Mapping) or set(price) != required_price_fields:
        raise PipelineError("model.price_snapshot has invalid fields")
    if not isinstance(price["date"], str) or not price["date"].strip():
        raise PipelineError("model.price_snapshot.date must be a non-empty string")
    if not isinstance(price["currency"], str) or not price["currency"].strip():
        raise PipelineError("model.price_snapshot.currency must be a non-empty string")
    for field_name in ("input_per_million_tokens", "output_per_million_tokens"):
        amount = price[field_name]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise PipelineError(f"model.price_snapshot.{field_name} must be numeric")
        if float(amount) < 0.0:
            raise PipelineError(f"model.price_snapshot.{field_name} cannot be negative")
    return copy.deepcopy(dict(value))


def _validate_frozen_snapshot_records(
    records: Sequence[Any], *, source_run_id: Any
) -> None:
    """Validate a new global snapshot before it can become model input."""

    try:
        validate_experience_snapshot(
            {"source_run_id": source_run_id, "records": list(records)}
        )
    except EvolutionError as exc:
        raise PipelineError(str(exc)) from exc


@dataclass(frozen=True)
class ExperimentConfig:
    """The small run configuration selected from ``configs/experiments``."""

    experiment_id: str
    robots: tuple[str, ...]
    generation_conditions: tuple[GenerationCondition, ...]
    phase_turn_budgets: Mapping[str, int] = field(
        default_factory=lambda: dict(PHASE_TURN_BUDGETS)
    )
    recap_max_planning_turns_per_task: int = RECAP_PLANNING_TURNS
    recap_max_capability_calls_per_task: int = RECAP_CAPABILITY_CALLS
    formal: bool = False
    max_driver_attempts_per_condition: int = MAX_TOTAL_ATTEMPTS
    probe_budget: ProbeBudget = ProbeBudget()
    record_video: bool = True
    worker_wall_timeout_s: float = 120.0
    model_manifest: Mapping[str, Any] | None = None
    experience_input: tuple[Mapping[str, Any], ...] = ()
    experience_snapshot_input: Mapping[str, Any] | None = None
    experience_snapshot_id: str | None = None
    experience_source_run_id: str | None = None
    experience_review_queue_output: str = "experience_review_queue.json"
    experience_snapshot_output: str = "experience_snapshot.json"
    task_demo_seed_template: str = "{run_id}:{robot_configuration_id}"
    experience_declared: bool = False
    seeds_declared: bool = False
    evolution_declared: bool = False
    evolution_enabled: bool = True
    evolution_max_attempts: int = 1
    evolution_model_manifest: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExperimentConfig":
        if not isinstance(value, Mapping):
            raise PipelineError("experiment configuration must be one JSON object")

        experiment_id = value.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise PipelineError("experiment_id must be a non-empty string")

        robots_value = value.get("robots")
        conditions_value = value.get("generation_conditions")
        if not isinstance(robots_value, list) or not robots_value:
            raise PipelineError("robots must be a non-empty list")
        if not all(isinstance(item, str) and item.strip() for item in robots_value):
            raise PipelineError("robots must contain non-empty strings")
        robots = tuple(str(item).strip() for item in robots_value)
        if len(set(robots)) != len(robots):
            raise PipelineError("robots must be distinct")

        if not isinstance(conditions_value, list) or not conditions_value:
            raise PipelineError("generation_conditions must be a non-empty list")
        if not all(
            isinstance(item, str) and item.strip() for item in conditions_value
        ):
            raise PipelineError("generation_conditions must contain non-empty strings")
        conditions = tuple(str(item).strip() for item in conditions_value)
        if len(set(conditions)) != len(conditions):
            raise PipelineError("generation_conditions must be distinct")
        unsupported = sorted(set(conditions) - set(DEFAULT_CONDITIONS))
        if unsupported:
            raise PipelineError(
                "unsupported generation_conditions: " + ", ".join(unsupported)
            )

        phase_turns = value.get("phase_turn_budgets", {})
        if not isinstance(phase_turns, Mapping):
            raise PipelineError("phase_turn_budgets must be an object")
        unknown_phase_budgets = sorted(set(phase_turns) - set(PHASE_TURN_BUDGETS))
        if unknown_phase_budgets:
            raise PipelineError(
                "phase_turn_budgets has unexpected fields: "
                + ", ".join(str(item) for item in unknown_phase_budgets)
            )
        for field_name, supplied in phase_turns.items():
            expected = PHASE_TURN_BUDGETS[str(field_name)]
            if supplied != expected:
                raise PipelineError(
                    f"phase_turn_budgets.{field_name} must equal {expected}"
                )
        fixed_phase_turns = dict(PHASE_TURN_BUDGETS)

        recap = value.get("recap", {})
        if not isinstance(recap, Mapping):
            raise PipelineError("recap must be an object")
        allowed_recap = {
            "max_planning_turns_per_task",
            "max_capability_calls_per_task",
        }
        unexpected_recap = sorted(set(recap) - allowed_recap)
        if unexpected_recap:
            raise PipelineError(
                "recap has unexpected fields: "
                + ", ".join(str(item) for item in unexpected_recap)
            )
        planning_turns = recap.get(
            "max_planning_turns_per_task", RECAP_PLANNING_TURNS
        )
        capability_calls = recap.get(
            "max_capability_calls_per_task", RECAP_CAPABILITY_CALLS
        )
        if planning_turns != RECAP_PLANNING_TURNS:
            raise PipelineError(
                f"recap.max_planning_turns_per_task must equal {RECAP_PLANNING_TURNS}"
            )
        if capability_calls != RECAP_CAPABILITY_CALLS:
            raise PipelineError(
                f"recap.max_capability_calls_per_task must equal {RECAP_CAPABILITY_CALLS}"
            )

        formal = value.get("formal", False)
        if not isinstance(formal, bool):
            raise PipelineError("formal must be boolean")

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
        execute_python = value.get("execute_python")
        if execute_python is not None and not isinstance(execute_python, Mapping):
            raise PipelineError("execute_python must be an object")
        if isinstance(execute_python, Mapping):
            expected_execute_fields = {
                "wall_timeout_s_per_call",
                "max_output_chars_per_call",
                "max_steps_per_phase",
                "max_sim_time_s_per_phase",
            }
            if set(execute_python) != expected_execute_fields:
                raise PipelineError(
                    "execute_python must declare wall timeout, output, step, "
                    "and simulated-time limits"
                )
        try:
            probe_budget = ProbeBudget(
                # File-workflow phases have no aggregate tool-call ceiling.  The
                # legacy development field is accepted but intentionally ignored;
                # every actual call remains bounded by time/output/physics.
                max_requests=None,
                max_complete_driver_checks=int(
                    development.get("max_complete_driver_checks", 1)
                ),
                timeout_s=float(
                    execute_python.get("wall_timeout_s_per_call", 30)
                    if isinstance(execute_python, Mapping)
                    else development.get("wall_timeout_s_per_request", 30)
                ),
                max_output_chars=int(
                    execute_python.get("max_output_chars_per_call", 12000)
                    if isinstance(execute_python, Mapping)
                    else development.get("max_output_chars_per_request", 12000)
                ),
                max_steps=int(
                    execute_python.get("max_steps_per_phase", 4000)
                    if isinstance(execute_python, Mapping)
                    else development.get("max_steps_per_stage", 4000)
                ),
                max_sim_time_s=float(
                    execute_python.get("max_sim_time_s_per_phase", 20.0)
                    if isinstance(execute_python, Mapping)
                    else development.get("max_sim_time_s_per_stage", 20.0)
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

        model_value = value.get("model")
        producer_model_value = value.get("producer_model")
        if model_value is not None and producer_model_value is not None:
            if model_value != producer_model_value:
                raise PipelineError("model and producer_model must match when both are supplied")
        if model_value is None:
            model_value = producer_model_value
        model_manifest = model_value
        if model_manifest is not None:
            model_manifest = _validated_model_manifest(model_manifest)

        experience_declared = "experience" in value
        experience_config = value.get("experience", {})
        if not isinstance(experience_config, Mapping):
            raise PipelineError("experience must be an object")
        experience_snapshot_id: str | None = None
        experience_source_run_id: str | None = None
        experience_snapshot_input: Mapping[str, Any] | None = None
        experience_input_value = experience_config.get("input", [])
        if isinstance(experience_input_value, Mapping):
            if experience_input_value.get("artifact_type") != "autoadapter_experience_snapshot":
                raise PipelineError(
                    "experience.input object must be a reviewed Experience snapshot"
                )
            if experience_input_value.get("review_status") not in {
                "reviewed",
                # Historical library snapshots predate the compact review status.
                "project_owner_reviewed",
            }:
                raise PipelineError("experience.input snapshot is not reviewed")
            usage_scope = experience_input_value.get("usage_scope")
            if usage_scope not in _EXPERIENCE_USAGE_SCOPES:
                raise PipelineError("experience.input snapshot has invalid usage scope")
            snapshot_id_value = experience_input_value.get("snapshot_id")
            if not isinstance(snapshot_id_value, str) or not snapshot_id_value.strip():
                raise PipelineError(
                    "experience.input snapshot.snapshot_id must be non-empty text"
                )
            experience_snapshot_id = snapshot_id_value.strip()
            source_run_id_value = experience_input_value.get("source_run_id")
            if isinstance(source_run_id_value, str) and source_run_id_value.strip():
                experience_source_run_id = source_run_id_value.strip()
            if usage_scope == "next_independent_run_only" and experience_input_value.get(
                "review_status"
            ) != "reviewed":
                raise PipelineError("experience.input snapshot is not reviewed")
            snapshot_records = experience_input_value.get("records")
            if snapshot_records is None and usage_scope == "later_matched_run_only":
                # Historical snapshots stored one list under each robot key.  Keep
                # those records consumable while the new global snapshot shape uses
                # one top-level ``records`` list.
                snapshot_records = []
                metadata_fields = {
                    "artifact_type",
                    "snapshot_id",
                    "version",
                    "review_status",
                    "usage_scope",
                    "b1_input",
                    "benefit_claim",
                    "source_run_id",
                }
                for key, candidate in experience_input_value.items():
                    if key in metadata_fields:
                        continue
                    if not isinstance(candidate, list):
                        raise PipelineError(
                            "legacy Experience snapshot robot records must be lists"
                        )
                    snapshot_records.extend(candidate)
            if snapshot_records is None:
                snapshot_records = []
            if not isinstance(snapshot_records, list):
                raise PipelineError("experience.input snapshot.records must be a list")
            if usage_scope == "next_independent_run_only":
                _validate_frozen_snapshot_records(
                    snapshot_records,
                    source_run_id=experience_input_value.get("source_run_id"),
                )
            # Preserve the reviewed container itself.  New snapshots use one
            # global records list; historical snapshots retain per-robot lists.
            experience_snapshot_input = _copy(dict(experience_input_value))
            experience_input_value = snapshot_records
        if not isinstance(experience_input_value, list):
            raise PipelineError("experience.input must be a list or reviewed snapshot")
        if not all(isinstance(item, Mapping) for item in experience_input_value):
            raise PipelineError("experience.input records must be objects")
        review_queue_output = experience_config.get(
            "review_queue_output", "experience_review_queue.json"
        )
        snapshot_output = experience_config.get(
            "snapshot_output", "experience_snapshot.json"
        )
        for field_name, output_name in (
            ("experience.review_queue_output", review_queue_output),
            ("experience.snapshot_output", snapshot_output),
        ):
            if (
                not isinstance(output_name, str)
                or not output_name.strip()
                or Path(output_name).name != output_name
            ):
                raise PipelineError(f"{field_name} must be one file name")

        seeds_declared = "seeds" in value
        seeds = value.get("seeds", {})
        if not isinstance(seeds, Mapping):
            raise PipelineError("seeds must be an object")
        seed_template = seeds.get(
            "task_demo_selection", "{run_id}:{robot_configuration_id}"
        )
        if not isinstance(seed_template, str) or not seed_template.strip():
            raise PipelineError("seeds.task_demo_selection must be a non-empty string")
        if seed_template != "{run_id}:{robot_configuration_id}":
            raise PipelineError(
                "seeds.task_demo_selection must be "
                "'{run_id}:{robot_configuration_id}'"
            )

        if "evolution_model" in value:
            raise PipelineError(
                "unsupported top-level evolution_model; use evolution.model"
            )
        evolution_declared = "evolution" in value
        evolution = value.get("evolution", {})
        if isinstance(evolution, bool):
            evolution = {"enabled": evolution}
        if not isinstance(evolution, Mapping):
            raise PipelineError("evolution must be an object")
        evolution_enabled = True
        evolution_max_attempts = 1
        evolution_model_manifest = None
        if evolution_declared:
            enabled_value = evolution.get("enabled", True)
            if not isinstance(enabled_value, bool):
                raise PipelineError("evolution.enabled must be boolean")
            evolution_enabled = enabled_value
            evolution_max_attempts = evolution.get("max_attempts", 1)
            if (
                isinstance(evolution_max_attempts, bool)
                or not isinstance(evolution_max_attempts, int)
                or evolution_max_attempts != 1
            ):
                raise PipelineError("evolution.max_attempts must equal 1")
            if "model_manifest" in evolution:
                raise PipelineError(
                    "unsupported evolution.model_manifest; use evolution.model"
                )
            evolution_model_value = evolution.get("model")
            if evolution_model_value is not None:
                evolution_model_manifest = _validated_model_manifest(
                    evolution_model_value
                )
            if evolution_enabled:
                expected_evolution = {
                    "after_each_terminal_cell": True,
                    "outcome_field": "cells[].outcomes.Evolution",
                }
                # The original two-field shape remains accepted.  The explicit enabled form
                # may add a terminal Evolution model manifest.
                actual_evolution = {
                    key: value
                    for key, value in evolution.items()
                    if key
                    not in {"enabled", "model", "model_manifest", "max_attempts"}
                }
                if actual_evolution != expected_evolution:
                    raise PipelineError(
                        "evolution must require each terminal cell and the canonical outcome field"
                    )
            else:
                allowed_disabled = {
                    "enabled",
                    "model",
                    "model_manifest",
                    "max_attempts",
                }
                unexpected_disabled = sorted(
                    str(key) for key in evolution if key not in allowed_disabled
                )
                if unexpected_disabled:
                    raise PipelineError(
                        "disabled evolution has unexpected fields: "
                        + ", ".join(unexpected_disabled)
                    )

        return cls(
            experiment_id=experiment_id.strip(),
            robots=robots,
            generation_conditions=conditions,  # type: ignore[arg-type]
            phase_turn_budgets=fixed_phase_turns,
            recap_max_planning_turns_per_task=planning_turns,
            recap_max_capability_calls_per_task=capability_calls,
            formal=formal,
            max_driver_attempts_per_condition=max_attempts,
            probe_budget=probe_budget,
            record_video=record_video,
            worker_wall_timeout_s=worker_timeout_value,
            model_manifest=model_manifest,
            experience_input=tuple(
                _copy(dict(item)) for item in experience_input_value
            ),
            experience_snapshot_input=experience_snapshot_input,
            experience_snapshot_id=experience_snapshot_id,
            experience_source_run_id=experience_source_run_id,
            experience_review_queue_output=review_queue_output.strip(),
            experience_snapshot_output=snapshot_output.strip(),
            task_demo_seed_template=seed_template,
            experience_declared=experience_declared,
            seeds_declared=seeds_declared,
            evolution_declared=evolution_declared,
            evolution_enabled=evolution_enabled,
            evolution_max_attempts=evolution_max_attempts,
            evolution_model_manifest=evolution_model_manifest,
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
        result: dict[str, Any] = {
            "experiment_id": self.experiment_id,
            "robots": list(self.robots),
            "generation_conditions": list(self.generation_conditions),
            "formal": self.formal,
            "phase_turn_budgets": dict(self.phase_turn_budgets),
            "recap": {
                "max_planning_turns_per_task": self.recap_max_planning_turns_per_task,
                "max_capability_calls_per_task": self.recap_max_capability_calls_per_task,
            },
            "max_driver_attempts_per_condition": self.max_driver_attempts_per_condition,
            "execute_python": {
                "wall_timeout_s_per_call": self.probe_budget.timeout_s,
                "max_output_chars_per_call": self.probe_budget.max_output_chars,
                "max_steps_per_phase": self.probe_budget.max_steps,
                "max_sim_time_s_per_phase": self.probe_budget.max_sim_time_s,
            },
            "validation": {
                "record_video": self.record_video,
                "worker_wall_timeout_s": self.worker_wall_timeout_s,
            },
        }
        if self.model_manifest is not None:
            result["model"] = _copy(dict(self.model_manifest))
        if self.experience_declared:
            result["experience"] = {
                "input": (
                    _copy(dict(self.experience_snapshot_input))
                    if self.experience_snapshot_input is not None
                    else [_copy(dict(item)) for item in self.experience_input]
                ),
                "review_queue_output": self.experience_review_queue_output,
                "snapshot_output": self.experience_snapshot_output,
            }
            if self.experience_snapshot_id is not None:
                result["experience"]["snapshot_id"] = self.experience_snapshot_id
            if self.experience_source_run_id is not None:
                result["experience"]["source_run_id"] = self.experience_source_run_id
        if self.seeds_declared:
            result["seeds"] = {
                "task_demo_selection": self.task_demo_seed_template,
            }
        if self.evolution_declared:
            if self.evolution_enabled:
                result["evolution"] = {
                    "after_each_terminal_cell": True,
                    "outcome_field": "cells[].outcomes.Evolution",
                    "max_attempts": self.evolution_max_attempts,
                }
                if self.evolution_model_manifest is not None:
                    result["evolution"]["model"] = _copy(
                        dict(self.evolution_model_manifest)
                    )
            else:
                result["evolution"] = {
                    "enabled": False,
                    "max_attempts": self.evolution_max_attempts,
                }
        return result


@dataclass(frozen=True)
class PipelineHooks:
    """Optional seams used by focused tests and small local experiments.

    The default hooks are the real mainline implementations. Test-only callers may
    replace them with explicit fake package/model/Harness functions without changing
    the dynamic production path.
    """

    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package
    capability_design_validator: Callable[..., Mapping[str, Any]] = (
        validate_capability_design
    )
    capability_suite_validator: Callable[..., Mapping[str, Any]] = (
        validate_capability_validation_suite
    )
    tgcd_runner: Callable[..., Mapping[str, Any]] = run_tgcd
    ivc_runner: Callable[..., Mapping[str, Any]] = run_ivc
    study_runner: Callable[..., StudyResult] = study
    probe_runner: Callable[..., Sequence[Mapping[str, Any]]] = run_probes
    generate_runner: Callable[..., GenerationResult] = generate
    repair_runner: Callable[..., Any] = repair_with_probes
    harness_runner: Callable[..., Mapping[str, Any]] = run_private_suite
    reference_renderer: Callable[..., Any] | None = None
    reference_runner: Callable[..., Mapping[str, Any]] | None = None
    recap_runner: Callable[..., Any] = run_recap
    task_demo_runner: Callable[..., Mapping[str, Any]] | None = None
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


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"cannot read {label} from {path}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"{label} must contain one JSON object")
    return value


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


def _validate_model_preflight(
    client: Any,
    manifest: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if manifest is None:
        return None
    runtime = getattr(client, "config", None)
    if runtime is None:
        raise PipelineError("manifest-pinned run requires a model client config")
    actual = {
        "vendor": getattr(runtime, "provider", None),
        "api_protocol": getattr(runtime, "api_protocol", None),
        "model_id": getattr(runtime, "model", None),
        "base_url": str(getattr(runtime, "base_url", "")).rstrip("/"),
        "max_output_tokens": getattr(runtime, "max_tokens", None),
        "thinking": getattr(runtime, "thinking", None) or "disabled",
        "tool_history_mode": getattr(runtime, "tool_history_mode", None),
        "temperature": 0.0,
    }
    expected = {
        "vendor": manifest["vendor"],
        "api_protocol": manifest["api_protocol"],
        "model_id": manifest["model_id"],
        "base_url": str(manifest["base_url"]).rstrip("/"),
        "max_output_tokens": manifest["max_output_tokens"],
        "thinking": manifest["thinking"] or "disabled",
        "tool_history_mode": manifest["tool_history_mode"],
        "temperature": float(manifest["temperature"]),
    }
    mismatches = [
        field_name
        for field_name in expected
        if actual[field_name] != expected[field_name]
    ]
    if mismatches:
        raise PipelineError(
            "runtime model differs from the prospective manifest: "
            + ", ".join(mismatches)
        )
    return {
        "matched": True,
        "runtime": actual,
        "manifest": _copy(dict(manifest)),
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
        react_trace = getattr(error, "react_trace", ())
        if isinstance(react_trace, Sequence) and not isinstance(
            react_trace, (str, bytes)
        ):
            result["react_trace"] = [
                _copy(dict(item)) for item in react_trace if isinstance(item, Mapping)
            ]
        probe_results = getattr(error, "probe_results", ())
        if isinstance(probe_results, Sequence) and not isinstance(
            probe_results, (str, bytes)
        ):
            result["probe_results"] = [
                _copy(dict(item)) for item in probe_results if isinstance(item, Mapping)
            ]
        model_turns = getattr(error, "model_turns", 0)
        tool_calls = getattr(error, "tool_calls", 0)
        if isinstance(model_turns, int) and model_turns > 0:
            result["react_model_turns"] = model_turns
        if isinstance(tool_calls, int) and tool_calls > 0:
            result["react_tool_calls"] = tool_calls
        candidate_path = getattr(error, "candidate_path", None)
        if isinstance(candidate_path, (str, Path)):
            result["candidate_path"] = str(candidate_path)
    return result


def _call_count(client: Any) -> int | None:
    calls = _client_calls(client)
    return len(calls) if calls is not None else None


def _experience_ids(experience: Sequence[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in experience:
        if not isinstance(item, Mapping):
            continue
        value = item.get("experience_id")
        provenance = item.get("provenance")
        if not isinstance(value, str) and isinstance(provenance, Mapping):
            value = provenance.get("experience_id")
        if isinstance(value, str) and value.strip():
            result.append(value)
    return result


def _with_experience_trace(
    evidence: Mapping[str, Any], experience: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    result = _copy(dict(evidence))
    result["experience_ids"] = _experience_ids(experience)
    return result


def _clone_client_for_manifest(client: Any, manifest: Mapping[str, Any]) -> Any | None:
    """Clone the built-in client for a second direct model manifest when possible."""

    config = getattr(client, "config", None)
    if config is None:
        return None
    try:
        from autoadapter2.model_api import JsonModelClient, ModelConfig

        cloned_config = ModelConfig(
            provider=str(manifest["vendor"]),
            model=str(manifest["model_id"]),
            base_url=str(manifest["base_url"]),
            api_key=str(getattr(config, "api_key")),
            api_protocol=str(manifest["api_protocol"]),
            auth_header=str(getattr(config, "auth_header", "Authorization")),
            auth_prefix=str(getattr(config, "auth_prefix", "Bearer ")),
            thinking=(
                None
                if manifest.get("thinking") in {None, "disabled"}
                else str(manifest["thinking"])
            ),
            timeout_s=float(getattr(config, "timeout_s", 180.0)),
            max_tokens=int(manifest["max_output_tokens"]),
            tool_history_mode=str(manifest["tool_history_mode"]),
            history_char_budget=int(getattr(config, "history_char_budget", 80000)),
        )
        return JsonModelClient(cloned_config)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _public_experience(
    experience: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    robot: str,
) -> tuple[Mapping[str, Any], ...]:
    if experience is None:
        return ()
    frozen_snapshot = False
    snapshot_source_run_id: str | None = None
    if isinstance(experience, Mapping):
        if "artifact_type" in experience:
            if experience.get("artifact_type") != "autoadapter_experience_snapshot":
                raise PipelineError("experience object must be an Experience snapshot")
            if experience.get("review_status") not in {
                "reviewed",
                "project_owner_reviewed",
            }:
                raise PipelineError("experience snapshot is not reviewed")
            usage_scope = experience.get("usage_scope")
            if usage_scope not in _EXPERIENCE_USAGE_SCOPES:
                raise PipelineError("experience snapshot has invalid usage scope")
            frozen_snapshot = usage_scope == "next_independent_run_only"
            if frozen_snapshot and experience.get("review_status") != "reviewed":
                raise PipelineError("experience snapshot is not reviewed")
            snapshot_id = experience.get("snapshot_id")
            if frozen_snapshot and (
                not isinstance(snapshot_id, str) or not snapshot_id.strip()
            ):
                raise PipelineError(
                    "experience snapshot.snapshot_id must be non-empty text"
                )
            source_run_id = experience.get("source_run_id")
            if frozen_snapshot and (
                not isinstance(source_run_id, str) or not source_run_id.strip()
            ):
                raise PipelineError(
                    "experience snapshot.source_run_id must be non-empty text"
                )
            snapshot_source_run_id = source_run_id if isinstance(source_run_id, str) else None
            if "records" in experience:
                value = experience.get("records", ())
            elif frozen_snapshot:
                raise PipelineError("experience snapshot.records must be a list")
            elif robot in experience:
                # Historical snapshots used one top-level list per robot.
                value = experience.get(robot, ())
            else:
                value = ()
            if frozen_snapshot:
                try:
                    validate_experience_snapshot(experience)
                except EvolutionError as exc:
                    raise PipelineError(str(exc)) from exc
        elif "records" in experience:
            value = experience.get("records", ())
        elif robot in experience:
            # Historical snapshots used one top-level list per robot.
            value = experience.get(robot, ())
        else:
            value = ()
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
        "source_robot",
        "generation_condition",
        "terminal_outcome_label",
        "source_condition",
        "source_outcome",
        "source_robot_configuration_id",
        "source_generation_condition",
        "outcome_label",
        "review_decision",
        "review_reason",
    }
    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise PipelineError(f"experience for {robot!r}[{index}] must be an object")
        if {
            "observation",
            "lesson",
            "recommendation",
            "scope",
            "public_evidence",
            "provenance",
            "outcome",
        } == set(item):
            if not frozen_snapshot:
                raise PipelineError(
                    f"experience for {robot!r}[{index}] must come from a reviewed snapshot"
                )
            # Frozen v2 records have already passed the exact validator above.
            # Keep the Framework-owned provenance/outcome wrappers intact; the
            # stage boundary, not a lossy schema rewrite, controls visibility.
            records.append(_copy(dict(item)))
            continue
        unexpected = sorted(str(key) for key in item if str(key) not in allowed_fields)
        if unexpected:
            raise PipelineError(
                f"experience for {robot!r}[{index}] contains unreviewed fields: "
                + ", ".join(unexpected)
            )
        if item.get("reviewed") is not True:
            raise PipelineError(f"experience for {robot!r}[{index}] is not reviewed")
        experience_id = item.get("experience_id")
        if not isinstance(experience_id, str) or not experience_id.strip():
            raise PipelineError(
                f"experience for {robot!r}[{index}].experience_id must be non-empty text"
            )
        review_decision = item.get("review_decision")
        if review_decision is not None and review_decision != "accept":
            raise PipelineError(
                f"experience for {robot!r}[{index}] must have an accepted review"
            )
        source_outcome = item.get(
            "terminal_outcome_label",
            item.get("source_outcome", item.get("outcome_label")),
        )
        if source_outcome is not None and source_outcome not in {"positive", "negative"}:
            raise PipelineError(
                f"experience for {robot!r}[{index}].source_outcome must be positive or negative"
            )
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
        if frozen_snapshot:
            required = (
                "experience_id",
                "source_run_id",
                "source_robot",
                "generation_condition",
                "terminal_outcome_label",
            )
            if any(
                not isinstance(item.get(field), str) or not item[field].strip()
                for field in required
            ):
                raise PipelineError(
                    f"experience for {robot!r}[{index}] is missing Framework lineage labels"
                )
            if item["source_run_id"] != snapshot_source_run_id:
                raise PipelineError(
                    f"experience for {robot!r}[{index}] has mismatched source_run_id"
                )
            if item["terminal_outcome_label"] not in {"positive", "negative"}:
                raise PipelineError(
                    f"experience for {robot!r}[{index}].terminal_outcome_label must be positive or negative"
                )
            if item.get("reviewed") is not True or item.get("review_decision") != "accept":
                raise PipelineError(
                    f"experience for {robot!r}[{index}] is not an accepted review"
                )
            review_reason = item.get("review_reason")
            if not isinstance(review_reason, str) or not review_reason.strip():
                raise PipelineError(
                    f"experience for {robot!r}[{index}] requires a review reason"
                )
        # Review metadata is retained in the frozen artifact for auditability but is not
        # model-authored input.  Only the public lesson content, stable ID, and Framework
        # source labels cross this boundary.
        public_item = {
            str(key): _copy(child)
            for key, child in item.items()
            if str(key) not in {"review_decision", "review_reason"}
        }
        if not frozen_snapshot:
            if "source_robot" not in public_item and "source_robot_configuration_id" in public_item:
                public_item["source_robot"] = public_item["source_robot_configuration_id"]
            if "generation_condition" not in public_item:
                legacy_condition = public_item.get(
                    "source_condition", public_item.get("source_generation_condition")
                )
                if isinstance(legacy_condition, str) and legacy_condition.strip():
                    public_item["generation_condition"] = legacy_condition
            if "terminal_outcome_label" not in public_item:
                legacy_outcome = public_item.get(
                    "source_outcome", public_item.get("outcome_label")
                )
                if isinstance(legacy_outcome, str) and legacy_outcome.strip():
                    public_item["terminal_outcome_label"] = legacy_outcome
        records.append(public_item)
    return tuple(records)


def _flatten_experience_records(value: Any) -> list[Mapping[str, Any]]:
    """Flatten global and historical per-robot containers for lineage checks."""

    if isinstance(value, Mapping):
        if "records" in value:
            return _flatten_experience_records(value.get("records"))
        if (
            "experience_id" in value
            or "source_run_id" in value
            or isinstance(value.get("provenance"), Mapping)
        ):
            return [value]
        records: list[Mapping[str, Any]] = []
        for child in value.values():
            if isinstance(child, (Mapping, Sequence)) and not isinstance(
                child, (str, bytes)
            ):
                records.extend(_flatten_experience_records(child))
        return records
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        records: list[Mapping[str, Any]] = []
        for child in value:
            records.extend(_flatten_experience_records(child))
        return records
    return []


def _experience_source_run_ids(
    experience: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> set[str]:
    """Return source IDs carried by a public Experience input."""

    source_ids = {
        str(
            item.get("source_run_id")
            if isinstance(item.get("source_run_id"), str)
            else item.get("provenance", {}).get("source_run_id")
        )
        for item in _flatten_experience_records(experience)
        if (
            isinstance(item.get("source_run_id"), str)
            and item.get("source_run_id")
        )
        or (
            isinstance(item.get("provenance"), Mapping)
            and isinstance(item["provenance"].get("source_run_id"), str)
            and item["provenance"].get("source_run_id")
        )
    }
    if isinstance(experience, Mapping):
        top_level_source_run_id = experience.get("source_run_id")
        if isinstance(top_level_source_run_id, str) and top_level_source_run_id.strip():
            source_ids.add(top_level_source_run_id.strip())
    return source_ids


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
    capability_suite: Mapping[str, Any],
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
        suite=_copy(dict(capability_suite)),
        driver_path=driver_path,
        condition=condition,
        trusted_reference_driver=True,
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
    evaluation_role: str,
) -> dict[str, Any]:
    result = _copy(dict(report))
    result.setdefault("robot_configuration_id", robot)
    result.setdefault("condition", condition)
    result.setdefault("attempt", attempt)
    result["evaluation_role"] = evaluation_role
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


def _without_experience(value: Any) -> Any:
    """Remove later-run Experience lineage from stages outside its visibility set."""

    if isinstance(value, Mapping):
        return {
            str(key): _without_experience(child)
            for key, child in value.items()
            if str(key)
            not in {
                "eligible_experience",
                "experience_input_ids",
                "experience_input_count",
                "experience_ids",
            }
        }
    if isinstance(value, list):
        return [_without_experience(child) for child in value]
    if isinstance(value, tuple):
        return [_without_experience(child) for child in value]
    return _copy(value)


def _private_artifact_event_summaries(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Describe private IVC trace structure without exposing its payloads."""

    summaries: list[dict[str, Any]] = []
    for event in events:
        summary: dict[str, Any] = {
            "stage": str(event.get("stage", "ivc")),
            "artifact_present": isinstance(event.get("artifact"), Mapping),
            "reference_calibration_present": isinstance(
                event.get("result"), Mapping
            ),
        }
        turn = event.get("turn")
        if isinstance(turn, int) and not isinstance(turn, bool) and turn >= 0:
            summary["turn"] = turn
        completion = event.get("completion")
        if isinstance(completion, str):
            summary["completion"] = completion
        tool_calls = event.get("tool_calls")
        if isinstance(tool_calls, int) and not isinstance(tool_calls, bool):
            summary["tool_call_count"] = max(0, tool_calls)
        trace = event.get("trace")
        if isinstance(trace, list):
            summary["trace_event_count"] = len(trace)
        summaries.append(summary)
    return summaries


def _artifact_error_trace(error: BaseException) -> list[dict[str, Any]]:
    """Recover a bounded ReAct trace retained on a wrapped phase error."""

    current: BaseException | None = error
    for _ in range(4):
        if current is None:
            break
        for attribute in ("react_trace", "trace"):
            value = getattr(current, attribute, None)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return [
                    _json_safe(dict(item))
                    for item in value
                    if isinstance(item, Mapping)
                ]
        current = current.__cause__ or current.__context__
    return []


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


def _passed_capability_ids(
    *,
    suite: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return only capabilities whose nominal and boundary cases both passed."""

    cases = suite.get("cases")
    trials = report.get("trials")
    if not isinstance(cases, list) or not isinstance(trials, list):
        return ()
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for trial in trials:
        if isinstance(trial, Mapping) and isinstance(trial.get("case_id"), str):
            by_case.setdefault(str(trial["case_id"]), []).append(trial)
    roles: dict[str, dict[str, bool]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        capability_id = case.get("capability_id")
        case_id = case.get("case_id")
        role = case.get("case_role")
        if (
            not isinstance(capability_id, str)
            or not isinstance(case_id, str)
            or role not in {"nominal", "calibrated_boundary"}
        ):
            continue
        values = by_case.get(case_id, [])
        roles.setdefault(capability_id, {})[str(role)] = bool(values) and all(
            bool(value.get("trial_passed")) for value in values
        )
    return tuple(
        sorted(
            capability_id
            for capability_id, outcomes in roles.items()
            if outcomes.get("nominal") is True
            and outcomes.get("calibrated_boundary") is True
        )
    )


def _whitelisted_design(
    design: Mapping[str, Any], capability_ids: Sequence[str]
) -> dict[str, Any]:
    allowed = set(capability_ids)
    result = _copy(dict(design))
    capabilities = result.get("capabilities")
    result["capabilities"] = (
        [
            capability
            for capability in capabilities
            if isinstance(capability, Mapping)
            and capability.get("capability_id") in allowed
        ]
        if isinstance(capabilities, list)
        else []
    )
    support = result.get("task_support")
    result["task_support"] = (
        [
            relation
            for relation in support
            if isinstance(relation, Mapping)
            and relation.get("capability_id") in allowed
        ]
        if isinstance(support, list)
        else []
    )
    return result


class _LegacyRecapJsonAdapter:
    """Retain the pre-native JSON seam for explicitly legacy test clients."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def generate_recap_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        direct = getattr(self.client, "generate_recap_json", None)
        if callable(direct):
            return direct(
                stage=stage,
                system_prompt=system_prompt,
                messages=messages,
                response_schema=response_schema,
            )
        message_json = getattr(self.client, "generate_message_json", None)
        if callable(message_json):
            schema_message = {
                "role": "user",
                "content": (
                    "Return exactly one JSON object matching this schema: "
                    + json.dumps(response_schema, sort_keys=True)
                ),
            }
            return message_json(
                stage=stage,
                system_prompt=system_prompt,
                messages=[*_copy(list(messages)), schema_message],
            )
        generate_json = getattr(self.client, "generate_json", None)
        if callable(generate_json):
            return generate_json(
                stage=stage,
                prompt=system_prompt,
                inputs={
                    "messages": _copy(list(messages)),
                    "response_schema": _copy(dict(response_schema)),
                },
            )
        raise PipelineError("unified model client has no ReCAP JSON turn")


class _RecapClientAdapter(_LegacyRecapJsonAdapter):
    """Forward the unified client's native dynamic-tool turn unchanged."""

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Any:
        native = getattr(self.client, "generate_tool_turn", None)
        if not callable(native):
            raise PipelineError("unified model client has no native tool-use turn")
        return native(
            stage=stage,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
        )


def _indexed_private(
    document: Mapping[str, Any], field: str, id_field: str
) -> dict[str, Mapping[str, Any]]:
    values = document.get(field)
    if not isinstance(values, list):
        raise PipelineError(f"private {field} must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping) or not isinstance(value.get(id_field), str):
            raise PipelineError(f"private {field} has an invalid {id_field}")
        identifier = str(value[id_field])
        if identifier in result:
            raise PipelineError(f"private {field} duplicates {identifier!r}")
        result[identifier] = value
    return result


def _compile_task_demo_inputs(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    seed: str,
    record_video: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select up to five supported public tasks and seal their trusted inputs."""

    supported = {
        str(relation["task_id"])
        for relation in design.get("task_support", [])
        if isinstance(relation, Mapping) and isinstance(relation.get("task_id"), str)
    }
    eligible = [
        task
        for task in package.tasks
        if isinstance(task, Mapping) and str(task.get("task_id")) in supported
    ]
    if not eligible:
        raise PipelineError("no Task Demo task is supported by the passed capability whitelist")
    selected = (
        random.Random(seed).sample(eligible, TASK_DEMO_TASK_COUNT)
        if len(eligible) >= TASK_DEMO_TASK_COUNT
        else list(eligible)
    )
    instances_document = _read_object(
        package.private_dir / "instances.json", label="Task Demo private instances"
    )
    bindings_document = _read_object(
        package.private_dir / "bindings.json", label="Task Demo private bindings"
    )
    guards_document = _read_object(
        package.private_dir / "guards.json", label="Task Demo private guards"
    )
    instances = list(_indexed_private(instances_document, "instances", "instance_id").values())
    bindings = _indexed_private(bindings_document, "bindings", "binding_id")
    guards = _indexed_private(guards_document, "guards", "guard_id")
    source_by_id = {
        str(source.get("source_id")): source
        for source in package.sources
        if isinstance(source, Mapping) and isinstance(source.get("source_id"), str)
    }
    task_records: list[dict[str, Any]] = []
    runtime_records: list[dict[str, Any]] = []
    for task in selected:
        task_id = str(task["task_id"])
        matches = sorted(
            (
                instance
                for instance in instances
                if instance.get("task_id") == task_id
            ),
            key=lambda value: str(value.get("instance_id", "")),
        )
        if not matches:
            raise PipelineError(f"Task Demo task {task_id!r} has no private instance")
        instance = matches[0]
        variants = instance.get("repetition_variants")
        variant = variants[0] if isinstance(variants, list) and variants else {}
        if not isinstance(variant, Mapping):
            raise PipelineError(f"Task Demo task {task_id!r} has an invalid variant")
        public_arguments = variant.get(
            "public_arguments", instance.get("public_arguments", {})
        )
        if not isinstance(public_arguments, Mapping) or not isinstance(
            public_arguments.get("request"), Mapping
        ):
            raise PipelineError(f"Task Demo task {task_id!r} lacks public arguments")
        request = _copy(dict(public_arguments["request"]))
        clause_bindings = instance.get("clause_bindings")
        guard_ids = instance.get("guard_ids")
        scoring = task.get("scoring")
        if (
            not isinstance(clause_bindings, Mapping)
            or not isinstance(guard_ids, list)
            or not isinstance(scoring, list)
            or not scoring
        ):
            raise PipelineError(f"Task Demo task {task_id!r} has incomplete trusted inputs")
        binding_records = []
        for binding_id in clause_bindings.values():
            if not isinstance(binding_id, str) or binding_id not in bindings:
                raise PipelineError(f"Task Demo task {task_id!r} has an invalid binding")
            binding_records.append(_copy(dict(bindings[binding_id])))
        guard_records = []
        for guard_id in guard_ids:
            if not isinstance(guard_id, str) or guard_id not in guards:
                raise PipelineError(f"Task Demo task {task_id!r} has an invalid guard")
            guard_records.append(_copy(dict(guards[guard_id])))
        source_ids = {
            str(reference.get("source_id"))
            for clause in scoring
            if isinstance(clause, Mapping)
            for reference in clause.get("source_refs", [])
            if isinstance(reference, Mapping) and isinstance(reference.get("source_id"), str)
        }
        source_records = [
            _copy(dict(source_by_id[source_id]))
            for source_id in sorted(source_ids)
            if source_id in source_by_id
        ]
        scene_entrypoint = str(
            variant.get(
                "scene_entrypoint",
                instance.get(
                    "scene_entrypoint", package.morphology["mjcf_entrypoint"]
                ),
            )
        )
        reset = variant.get("reset", instance.get("reset", {"kind": "default"}))
        public_task = {
            "task_id": task_id,
            "task_name": str(task.get("name", task_id)),
            "objective": str(task.get("description", task.get("name", task_id))),
            "task_parameters": _copy(request.get("task_parameters", {})),
        }
        task_record = {
            "task_id": task_id,
            "private_instance_id": str(instance["instance_id"]),
            "public_projection": {
                "task_id": task_id,
                "name": public_task["task_name"],
                "objective": public_task["objective"],
                "request": request,
            },
            "private_scoring_clauses": _copy(scoring),
            "private_clause_bindings": _copy(dict(clause_bindings)),
            "private_measurement_bindings": binding_records,
            "private_guards": guard_records,
            "source_records": source_records,
            "episode_budget": {
                "timeout_sim_s": float(instance.get("timeout_sim_s", 20.0)),
                "max_steps": int(instance.get("max_steps", 10_000)),
                "sample_hz": float(instance.get("sample_hz", 20.0)),
            },
            "rendering": {
                "enabled": record_video,
                "continuous_episode_video_required": record_video,
                "video_fps": float(instance.get("video_fps", 10.0)),
                "video_width": int(instance.get("video_width", 800)),
                "video_height": int(instance.get("video_height", 600)),
                "camera": instance.get("camera", -1),
            },
            "replicate_inputs": [
                {
                    "replicate_id": "mainline",
                    "scene_entrypoint": scene_entrypoint,
                    "reset": _copy(reset),
                    "reset_seed": None,
                    "reset_seed_applied": False,
                }
            ],
        }
        task_records.append(task_record)
        runtime_records.append(
            {
                "task_id": task_id,
                "instance_id": str(instance["instance_id"]),
                "public_task": public_task,
                "scene_entrypoint": scene_entrypoint,
                "reset": _copy(reset),
                "max_steps": int(instance.get("max_steps", 10_000)),
                "max_sim_time_s": float(instance.get("timeout_sim_s", 20.0)),
                "sample_hz": float(instance.get("sample_hz", 20.0)),
                "render": task_record["rendering"],
            }
        )
    suite = {
        "artifact_type": "b2_recap_task_suite",
        "schema_version": "1.0",
        "authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "robot_suites": [
            {
                "robot_configuration_id": package.robot_configuration_id,
                "package_version": package.package_version,
                "task_snapshot_id": package.snapshot_id,
                "tasks": task_records,
            }
        ],
    }
    return suite, runtime_records


def _default_task_demo_run(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    driver_path: Path,
    client: Any,
    recap_runner: Callable[..., Any],
    budgets: RecapBudgets,
    selection_seed: str,
    output_dir: Path,
    record_video: bool,
    wall_timeout_s: float,
    **_: Any,
) -> Mapping[str, Any]:
    """Run canonical ReCAP in persistent workers, then a trusted task Harness."""

    # The compatibility session runner imports the same function object re-exported
    # from task_demo.recap.  Fail closed if a caller replaces the canonical hook.
    if recap_runner is not run_recap:
        raise PipelineError("Task Demo must use canonical task_demo.recap.run_recap")
    from autoadapter2.b2.session_runner import (
        RecapWorkerSessionConfig,
        run_recap_worker_session,
    )
    from autoadapter2.b2.task_harness import evaluate_b2_task_harness

    output_dir.mkdir(parents=True, exist_ok=True)
    suite, tasks = _compile_task_demo_inputs(
        package=package,
        design=design,
        seed=selection_seed,
        record_video=record_video,
    )
    suite_path = output_dir / "framework_task_inputs.json"
    _write(suite_path, suite)
    write_capability_design(output_dir / "capability_design.json", design)
    model = (
        _RecapClientAdapter(client)
        if callable(getattr(client, "generate_tool_turn", None))
        else _LegacyRecapJsonAdapter(client)
    )
    trials: list[dict[str, Any]] = []
    planning_turns = 0
    capability_calls = 0
    controller_completed: list[bool] = []
    for task in tasks:
        scene_path = (package.root / str(task["scene_entrypoint"])).resolve()
        try:
            scene_path.relative_to((package.root / "assets").resolve())
        except ValueError as exc:
            raise PipelineError("Task Demo scene escapes package assets") from exc
        video_path = (
            output_dir / "videos" / f"{task['task_id']}__mainline.mp4"
            if record_video
            else None
        )
        session = run_recap_worker_session(
            config=RecapWorkerSessionConfig(
                driver_path=driver_path,
                scene_path=scene_path,
                robot_configuration_id=package.robot_configuration_id,
                reset=task["reset"],
                max_steps=int(task["max_steps"]),
                max_sim_time_s=float(task["max_sim_time_s"]),
                sample_hz=float(task["sample_hz"]),
                wall_timeout_s=wall_timeout_s,
                render={
                    "enabled": record_video,
                    "width": int(task["render"].get("video_width", 800)),
                    "height": int(task["render"].get("video_height", 600)),
                    "fps": float(task["render"].get("video_fps", 10.0)),
                    "camera": task["render"].get("camera", -1),
                },
                video_path=video_path,
            ),
            capability_design=design,
            public_task=task["public_task"],
            model=model,
            budgets=budgets,
        )
        harness = evaluate_b2_task_harness(
            package=package,
            task_suite_path=suite_path,
            instance_id=str(task["instance_id"]),
            replicate_id="mainline",
            session_result=session,
        )
        controller = session.get("controller", {})
        if isinstance(controller, Mapping):
            planning_turns += int(
                controller.get("planning_turns", controller.get("model_turns", 0))
            )
            capability_calls += int(
                controller.get(
                    "capability_calls", controller.get("tool_calls", 0)
                )
            )
            controller_completed.append(
                controller.get("status") == "CONTROLLER_FINISHED"
                and int(
                    controller.get(
                        "capability_calls", controller.get("tool_calls", 0)
                    )
                )
                >= 1
            )
        trials.append(
            {
                "case_id": f"task-demo-{task['task_id']}",
                "task_id": task["task_id"],
                "source_clause_id": "all-task-clauses",
                "trial_passed": harness.get("physical_harness_verdict") == "PASS",
                "physical_execution_passed": bool(harness.get("physical_execution_passed")),
                "video": (
                    _copy(dict(harness.get("video", {})))
                    if isinstance(harness.get("video"), Mapping)
                    else {}
                ),
                "controller": _copy(dict(controller)) if isinstance(controller, Mapping) else {},
                "harness": _copy(dict(harness)),
            }
        )
    physical_executed = bool(trials) and all(
        bool(item["physical_execution_passed"]) for item in trials
    )
    video_complete = bool(trials) and all(
        (not record_video) or bool(item["video"].get("complete")) for item in trials
    )
    return {
        "pipeline_completed": bool(trials),
        "physical_validation_executed": physical_executed,
        "validation_passed": bool(trials) and all(bool(item["trial_passed"]) for item in trials),
        "video_complete": video_complete,
        "task_count": len(trials),
        "passed_task_count": sum(bool(item["trial_passed"]) for item in trials),
        "trials": trials,
        "video_manifest": [item["video"] for item in trials],
        "high_level_controller": {
            "kind": "task_demo_recap",
            "path_enabled": True,
            "completed": bool(controller_completed) and all(controller_completed),
            "model_turn_count": planning_turns,
            "capability_call_count": capability_calls,
        },
    }


def _run_cell(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    capability_suite: Mapping[str, Any],
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
    completed_study: StudyResult | None = None,
    completed_probe_results: Sequence[Mapping[str, Any]] = (),
    evolution_client: Any | None = None,
    evolution_enabled: bool = True,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    terminal_evolution_client = evolution_client if evolution_client is not None else client
    experience_ids = _experience_ids(experience)
    _write(
        workspace / "experience_input.json",
        {"experience_ids": experience_ids, "records": _copy(list(experience))},
    )
    runtime_contract = _runtime_contract(package)
    public_design = _copy(dict(design))
    sealed_capability_suite = _copy(dict(capability_suite))
    attempts: list[dict[str, Any]] = []
    development_rejections: list[dict[str, Any]] = []
    driver_generated = False
    dynamic_model_called = True
    initial_pass: bool | None = None
    terminal_validation: dict[str, Any] | None = None
    current_driver: Path | None = None
    current_source: str | None = None
    if completed_study is None:
        raise PipelineError("Driver generation requires the completed pre-TGCD STUDY")
    study_result = completed_study
    probe_results: tuple[Mapping[str, Any], ...] = tuple(
        _copy(dict(item)) for item in completed_probe_results
    )
    failure: dict[str, Any] | None = None
    if not _has_successful_physics_probe(probe_results):
        raise PipelineError("completed STUDY has no successful real-MuJoCo probe")
    _write(workspace / "probe_results.json", {"results": list(probe_results)})

    if study_result is not None and failure is None:
        for attempt in range(config.max_driver_attempts_per_condition):
            attempt_dir = workspace / f"attempt-{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            before: int | None = None
            generation_evidence: dict[str, Any] | None = None
            repair_evidence: dict[str, Any] | None = None
            try:
                if attempt == 0:
                    before = _call_count(client)
                    generated = _call_supported(
                        hooks.generate_runner,
                        client,
                        package,
                        public_design,
                        study_result,
                        condition=condition,
                        workspace=workspace / "files",
                        probe_results=probe_results,
                        experience=experience,
                        runtime_contract=runtime_contract,
                        probe_budget=config.probe_budget,
                        source_root=_default_root() / "src",
                        max_turns=int(
                            config.phase_turn_budgets[
                                "generate_skeleton"
                                if condition == "skeleton-assisted"
                                else "generate_from_scratch"
                            ]
                        ),
                    )
                    generation_evidence = _stage_evidence(
                        client,
                        stage="generate",
                        before=before,
                        completed=True,
                    )
                    model_stage_log.append(
                        {
                            "robot": robot,
                            "condition": condition,
                            **_with_experience_trace(generation_evidence, experience),
                        }
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
                        "candidate_report": attempts[-1]["capability_validation"],
                        "media_manifest": attempts[-1]["capability_validation"].get(
                            "video_manifest", []
                        ),
                        "public_inputs": public_inputs,
                        "condition": condition,
                        "previous_attempt": attempt - 1,
                        "workspace": workspace / "files",
                        "max_total_attempts": config.max_driver_attempts_per_condition,
                        "max_turns": int(
                            config.phase_turn_budgets[
                                "repair_skeleton"
                                if condition == "skeleton-assisted"
                                else "repair_from_scratch"
                            ]
                        ),
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
                    repaired = _call_supported(
                        hooks.repair_runner,
                        client,
                        **repair_kwargs,
                    )
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
                        {
                            "robot": robot,
                            "condition": condition,
                            **_with_experience_trace(repair_evidence, experience),
                        }
                    )
                    generated = repaired
                    driver_generated = True

                candidate_driver = Path(generated.driver_path).resolve()
                current_source = str(generated.driver_source)
                frozen_dir = workspace / "frozen-driver-attempts" / f"attempt-{attempt + 1}"
                frozen_dir.mkdir(parents=True, exist_ok=True)
                current_driver = frozen_dir / "driver.py"
                shutil.copyfile(candidate_driver, current_driver)
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
                        "driver_frozen": True,
                        "frozen_driver_path": str(current_driver),
                    },
                )
                failure = None
            except DriverSourceAuditError as exc:
                stage = "generate" if attempt == 0 else "repair"
                retained_prior_driver = bool(
                    attempt > 0
                    and current_driver is not None
                    and terminal_validation is not None
                    and attempts
                )
                evidence = _stage_evidence(
                    client,
                    stage=stage,
                    before=before,
                    completed=False,
                    error=exc,
                )
                model_stage_log.append(
                    {
                        "robot": robot,
                        "condition": condition,
                        **_with_experience_trace(evidence, experience),
                    }
                )
                if not retained_prior_driver:
                    current_driver = None
                    current_source = exc.driver_source
                driver_generated = True
                failure = {"stage": stage, **_failure_record(exc)}
                rejection = {
                    "stage": stage,
                    "formal_attempt_submitted": False,
                    "source_audit_passed": False,
                    "previous_frozen_driver_retained": retained_prior_driver,
                    "failure": failure,
                    "evidence": evidence,
                }
                development_rejections.append(rejection)
                if not retained_prior_driver:
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
                        evaluation_role="capability_validation",
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
                        "previous_frozen_driver_retained": retained_prior_driver,
                        **(
                            {"retained_frozen_driver_path": str(current_driver)}
                            if retained_prior_driver and current_driver is not None
                            else {}
                        ),
                    },
                )
                _write(attempt_dir / "generation_error.json", failure)
                break
            except Exception as exc:
                stage = "generate" if attempt == 0 else "repair"
                evidence = _stage_evidence(
                    client,
                    stage=stage,
                    before=before,
                    completed=False,
                    error=exc,
                )
                model_stage_log.append(
                    {
                        "robot": robot,
                        "condition": condition,
                        **_with_experience_trace(evidence, experience),
                    }
                )
                failure = {"stage": stage, **_failure_record(exc)}
                candidate_preserved = False
                candidate_path = getattr(exc, "candidate_path", None)
                if isinstance(candidate_path, (str, Path)):
                    candidate: Path | None = Path(candidate_path).resolve()
                    try:
                        candidate.relative_to(attempt_dir.resolve())
                    except ValueError:
                        candidate = None
                    if candidate is not None and candidate.is_file():
                        try:
                            current_source = candidate.read_text(encoding="utf-8")
                            candidate_preserved = bool(current_source.strip())
                            driver_generated = driver_generated or candidate_preserved
                        except (OSError, UnicodeDecodeError):
                            candidate_preserved = False
                development_rejections.append(
                    {
                        "stage": stage,
                        "formal_attempt_submitted": False,
                        "source_audit_passed": None,
                        "candidate_preserved": candidate_preserved,
                        "failure": failure,
                        "evidence": evidence,
                    }
                )
                _write(
                    attempt_dir / "generation_evidence.json",
                    {
                        "attempt": attempt,
                        "driver_filename": "driver.py",
                        "evidence": evidence,
                        "development_probe_results": evidence.get("probe_results", []),
                        "candidate_preserved": candidate_preserved,
                        "formal_attempt_submitted": False,
                    },
                )
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
                    suite=_copy(sealed_capability_suite),
                    driver_path=current_driver,
                    condition=condition,
                    output_dir=attempt_dir / "capability-validation",
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
                    evaluation_role="capability_validation",
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
                    evaluation_role="capability_validation",
                )
            terminal_validation = validation
            if initial_pass is None:
                initial_pass = bool(validation.get("validation_passed"))
            attempt_record: dict[str, Any] = {
                "attempt": attempt,
                "driver_generated": True,
                "driver_frozen": True,
                "frozen_driver_path": str(current_driver),
                "capability_validation": validation,
            }
            if generation_evidence is not None:
                attempt_record["generate"] = generation_evidence
            if repair_evidence is not None:
                attempt_record["repair"] = repair_evidence
            attempts.append(attempt_record)
            _write(attempt_dir / "capability_validation_report.json", validation)
            if bool(validation.get("validation_passed")):
                break

    if terminal_validation is None:
        terminal_validation = _normalise_validation_report(
            {
                "pipeline_completed": False,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_complete": not config.record_video,
                "trials": [],
                "video_manifest": [],
            },
            robot=robot,
            condition=condition,
            attempt=max(0, len(attempts) - 1),
            record_video=config.record_video,
            evaluation_role="capability_validation",
        )

    final_pass = bool(terminal_validation.get("validation_passed"))
    passed_capability_ids = _passed_capability_ids(
        suite=sealed_capability_suite,
        report=terminal_validation,
    )
    task_demo = _normalise_validation_report(
        {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "video_complete": not config.record_video,
            "skipped": True,
            "skip_reason": "no capability passed both nominal and calibrated-boundary cases",
            "trials": [],
            "video_manifest": [],
        },
        robot=robot,
        condition=condition,
        attempt=max(0, len(attempts) - 1),
        record_video=config.record_video,
        evaluation_role="task_demo",
    )
    if passed_capability_ids and current_driver is not None:
        task_demo_attempt = int(terminal_validation.get("attempt", 0))
        controller_before = _call_count(client)
        try:
            task_demo_runner = hooks.task_demo_runner or _default_task_demo_run
            task_demo_raw = _call_supported(
                task_demo_runner,
                package=package,
                design=_whitelisted_design(public_design, passed_capability_ids),
                driver_path=current_driver,
                client=client,
                recap_runner=hooks.recap_runner,
                budgets=RecapBudgets(
                    max_planning_turns=config.recap_max_planning_turns_per_task,
                    max_capability_calls=config.recap_max_capability_calls_per_task,
                ),
                selection_seed=config.task_demo_seed_template.format(
                    run_id=run_id,
                    robot_configuration_id=robot,
                ),
                capability_whitelist=passed_capability_ids,
                condition=condition,
                output_dir=workspace / "task-demo",
                record_video=config.record_video,
                wall_timeout_s=config.worker_wall_timeout_s,
                run_id=run_id,
                attempt=task_demo_attempt,
            )
            controller_summary = task_demo_raw.get("high_level_controller")
            controller_stage_completed = (
                bool(controller_summary.get("completed"))
                if isinstance(controller_summary, Mapping)
                else True
            )
            model_stage_log.append(
                {
                    "robot": robot,
                    "condition": condition,
                    **_stage_evidence(
                        client,
                        stage="task_demo_recap",
                        before=controller_before,
                        completed=controller_stage_completed,
                    ),
                    "experience_ids": [],
                    "capability_whitelist": list(passed_capability_ids),
                }
            )
            task_demo = _normalise_validation_report(
                task_demo_raw,
                robot=robot,
                condition=condition,
                attempt=task_demo_attempt,
                record_video=config.record_video,
                evaluation_role="task_demo",
            )
        except Exception as exc:
            model_stage_log.append(
                {
                    "robot": robot,
                    "condition": condition,
                    **_stage_evidence(
                        client,
                        stage="task_demo_recap",
                        before=controller_before,
                        completed=False,
                        error=exc,
                    ),
                    "experience_ids": [],
                    "capability_whitelist": list(passed_capability_ids),
                }
            )
            task_demo = _normalise_validation_report(
                {
                    "pipeline_completed": False,
                    "physical_validation_executed": False,
                    "validation_passed": False,
                    "video_complete": not config.record_video,
                    "failure": _failure_record(exc),
                    "trials": [],
                    "video_manifest": [],
                },
                robot=robot,
                condition=condition,
                attempt=task_demo_attempt,
                record_video=config.record_video,
                evaluation_role="task_demo",
            )
        _write(workspace / "task-demo" / "task_demo_report.json", task_demo)

    cell_pipeline_completed = bool(terminal_validation.get("pipeline_completed")) and (
        not passed_capability_ids or bool(task_demo.get("pipeline_completed"))
    )
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
                str(relation["task_id"])
                for relation in public_design.get("task_support", [])
                if isinstance(relation, Mapping)
                and isinstance(relation.get("task_id"), str)
            }
        ),
        "condition": condition,
        "provider": identity["provider"],
        "model": identity["model"],
        "experience_input_ids": experience_ids,
        "experience_input_count": len(experience_ids),
        "pipeline_completed": cell_pipeline_completed,
        "dynamic_model_called": dynamic_model_called,
        "driver_generated_in_run": driver_generated,
        "capability_validation_executed": bool(
            terminal_validation.get("physical_validation_executed")
        ),
        "initial_capability_validation_passed": bool(initial_pass),
        "final_capability_validation_passed": final_pass,
        "task_demo_executed": bool(task_demo.get("physical_validation_executed")),
        "task_demo_passed": bool(task_demo.get("validation_passed")),
        "task_demo_pipeline_completed": bool(task_demo.get("pipeline_completed")),
        "task_demo_video_complete": bool(task_demo.get("video_complete")),
        # Compatibility aliases map only to Capability Validation.
        "physical_validation_executed": bool(
            terminal_validation.get("physical_validation_executed")
        ),
        "initial_validation_passed": bool(initial_pass),
        "final_validation_passed": final_pass,
        "video_required": config.record_video,
        "video_complete": bool(terminal_validation.get("video_complete")),
        "attempts": attempts,
        "frozen_driver_attempt_count": len(attempts),
        "passed_capability_whitelist": list(passed_capability_ids),
        "development_rejections": development_rejections,
        "development_probe": {
            "attempted": bool(study_result and study_result.probe_requests),
            "successful_physics_probe": _has_successful_physics_probe(probe_results),
            "results": list(probe_results),
        },
        "trials": terminal_validation.get("trials", []),
        "video_manifest": terminal_validation.get("video_manifest", []),
        "capability_validation": _copy(terminal_validation),
        "task_demo": _copy(task_demo),
        "task_demo_task_counts": {
            "passed": int(task_demo.get("passed_task_count", 0)),
            "total": int(task_demo.get("task_count", 0)),
        },
        "task_demo_trials": task_demo.get("trials", []),
        "task_demo_video_manifest": task_demo.get("video_manifest", []),
        "failure": failure,
        "outcomes": {
            "TGCD": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "tgcd"
                    and item.get("robot") == robot
                    and item.get("condition") == condition
                ),
                None,
            ),
            "IVC": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "ivc"
                    and item.get("robot") == robot
                    and item.get("condition") == condition
                ),
                None,
            ),
            "STUDY": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "study"
                    and item.get("robot") == robot
                    and item.get("condition") == condition
                ),
                None,
            ),
            "GENERATE": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "generate"
                    and item.get("robot") == robot
                    and item.get("condition") == condition
                ),
                None,
            ),
            "CapabilityValidation": _copy(terminal_validation),
            "TaskDemoController": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "task_demo_recap"
                    and item.get("robot") == robot
                    and item.get("condition") == condition
                ),
                None,
            ),
            "TaskDemo": _copy(task_demo),
            "Repair": [
                item
                for item in model_stage_log
                if item.get("stage") == "repair"
                and item.get("robot") == robot
                and item.get("condition") == condition
            ],
        },
    }

    if evolution_enabled:
        try:
            evolution = hooks.evolution_runner(
                terminal_evolution_client,
                _without_experience(raw_report),
            )
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
    else:
        raw_report["evolution"] = None
        raw_report["evolution_disabled"] = True
    # Keep the raw per-cell artifact on the same canonical outcome path used by
    # the public experiment report and evidence checkers.  ``evolution`` stays
    # as a compatibility alias for existing run readers.
    raw_report["outcomes"]["Evolution"] = _copy(raw_report["evolution"])
    _write(workspace / "cell_report.json", raw_report)
    return raw_report


def _pre_driver_failure_cell(
    *,
    package: RobotPackage,
    robot: str,
    condition: GenerationCondition,
    config: ExperimentConfig,
    identity: Mapping[str, str],
    experience: Sequence[Mapping[str, Any]],
    workspace: Path,
    failed_stage: str,
    failure_detail: Mapping[str, str],
    model_stage_log: Sequence[Mapping[str, Any]],
    completed_study: StudyResult | None,
    completed_probe_results: Sequence[Mapping[str, Any]],
    design: Mapping[str, Any] | None,
    hooks: PipelineHooks,
    evolution_client: Any | None,
    evolution_enabled: bool,
) -> dict[str, Any]:
    """Retain one failed pre-Driver cell without aborting later cells."""

    failure = {
        "stage": failed_stage,
        "robot": robot,
        "condition": condition,
        **_copy(dict(failure_detail)),
    }
    skip_reason = f"{failed_stage.upper()} did not complete; Driver generation was not run"
    capability_validation = _normalise_validation_report(
        {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "skipped": True,
            "skip_reason": skip_reason,
            "failure": _copy(failure),
            "trials": [],
            "video_manifest": [],
        },
        robot=robot,
        condition=condition,
        attempt=0,
        record_video=config.record_video,
        evaluation_role="capability_validation",
    )
    task_demo = _normalise_validation_report(
        {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "skipped": True,
            "skip_reason": skip_reason,
            "trials": [],
            "video_manifest": [],
        },
        robot=robot,
        condition=condition,
        attempt=0,
        record_video=config.record_video,
        evaluation_role="task_demo",
    )
    stage_outcomes = {
        stage: next(
            (
                _copy(dict(item))
                for item in reversed(model_stage_log)
                if item.get("stage") == stage
                and item.get("robot") == robot
                and item.get("condition") == condition
            ),
            None,
        )
        for stage in ("study", "tgcd", "ivc")
    }
    capabilities = (
        [item for item in design.get("capabilities", []) if isinstance(item, Mapping)]
        if isinstance(design, Mapping)
        else []
    )
    support = design.get("task_support", []) if isinstance(design, Mapping) else []
    probe_results = [
        _copy(dict(item))
        for item in completed_probe_results
        if isinstance(item, Mapping)
    ]
    experience_ids = _experience_ids(experience)
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
                str(item["task_id"])
                for item in support
                if isinstance(item, Mapping) and isinstance(item.get("task_id"), str)
            }
        ),
        "condition": condition,
        "provider": identity["provider"],
        "model": identity["model"],
        "experience_input_ids": experience_ids,
        "experience_input_count": len(experience_ids),
        "pipeline_completed": False,
        "dynamic_model_called": True,
        "driver_generated_in_run": False,
        "capability_validation_executed": False,
        "initial_capability_validation_passed": False,
        "final_capability_validation_passed": False,
        "task_demo_executed": False,
        "task_demo_passed": False,
        "task_demo_pipeline_completed": False,
        "task_demo_video_complete": bool(task_demo.get("video_complete")),
        "physical_validation_executed": False,
        "initial_validation_passed": False,
        "final_validation_passed": False,
        "video_required": config.record_video,
        "video_complete": bool(capability_validation.get("video_complete")),
        "attempts": [],
        "frozen_driver_attempt_count": 0,
        "passed_capability_whitelist": [],
        "development_rejections": [],
        "development_probe": {
            "attempted": bool(completed_study and completed_study.probe_requests),
            "successful_physics_probe": _has_successful_physics_probe(probe_results),
            "results": probe_results,
        },
        "trials": [],
        "video_manifest": [],
        "capability_validation": capability_validation,
        "task_demo": task_demo,
        "task_demo_trials": [],
        "task_demo_video_manifest": [],
        "failure": failure,
        "outcomes": {
            "TGCD": stage_outcomes["tgcd"],
            "IVC": stage_outcomes["ivc"],
            "STUDY": stage_outcomes["study"],
            "GENERATE": None,
            "CapabilityValidation": _copy(capability_validation),
            "Repair": [],
            "TaskDemoController": None,
            "TaskDemo": _copy(task_demo),
        },
    }
    if evolution_enabled:
        try:
            evolution = hooks.evolution_runner(
                evolution_client,
                _without_experience(raw_report),
            )
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
    else:
        raw_report["evolution"] = None
        raw_report["evolution_disabled"] = True
    raw_report["outcomes"]["Evolution"] = _copy(raw_report["evolution"])
    _write(workspace / "cell_report.json", raw_report)
    return raw_report


def load_experiment_packages(
    mainline_root: str | Path,
    config: ExperimentConfig,
    *,
    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package,
    check_self_containment: bool = True,
) -> tuple[dict[str, RobotPackage], dict[str, Any]]:
    """Load every indexed package before any model-authored call."""

    root = Path(mainline_root).resolve()
    self_containment: dict[str, Any] = {}
    if check_self_containment:
        try:
            self_containment = check_self_contained(root)
        except Exception as exc:
            raise PipelineError(f"mainline self-containment check failed: {exc}") from exc
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
    mainline_root: str | Path,
    *,
    config: ExperimentConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    package_loader: Callable[..., RobotPackage] = load_indexed_robot_package,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Package/check-only entry point; it never constructs a model client."""

    root = Path(mainline_root).resolve()
    if config is None:
        config = ExperimentConfig.from_path(config_path or root / DEFAULT_CONFIG_PATH)
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
        "mainline_root": str(root),
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


def _run_study_phase(
    *,
    package: RobotPackage,
    robot: str,
    condition: GenerationCondition,
    config: ExperimentConfig,
    client: Any,
    experience: Sequence[Mapping[str, Any]],
    workspace: Path,
    hooks: PipelineHooks,
    stage_log: list[dict[str, Any]],
) -> tuple[StudyResult, tuple[Mapping[str, Any], ...]]:
    """Execute package-only STUDY before any fresh TGCD call."""

    before = _call_count(client)
    try:
        result = _call_supported(
            hooks.study_runner,
            client,
            package,
            design=None,
            condition=condition,
            experience=experience,
            runtime_contract=_runtime_contract(package),
            workspace=workspace / "files",
            probe_budget=config.probe_budget,
            source_root=_default_root() / "src",
            max_turns=int(config.phase_turn_budgets["study"]),
        )
        if not isinstance(result, StudyResult):
            raise PipelineError("STUDY runner must return StudyResult")
        probe_results = tuple(
            _copy(dict(item))
            for item in getattr(result, "probe_results", ())
            if isinstance(item, Mapping)
        )
        if not probe_results:
            raw = hooks.probe_runner(
                result.probe_requests,
                package=package,
                workspace=workspace / "files" / "study-probe",
                condition=condition,
                budget=config.probe_budget,
                source_root=_default_root() / "src",
            )
            probe_results = tuple(_copy(dict(item)) for item in raw)
        if not _has_successful_physics_probe(probe_results):
            raise ProbeError("STUDY produced no successful real-MuJoCo physics probe")
        canonical_path = workspace / "files" / "study.json"
        if not canonical_path.is_file():
            _write(canonical_path, result.output)
        evidence = _with_experience_trace(
            _stage_evidence(client, stage="study", before=before, completed=True),
            experience,
        )
        stage_log.append({"robot": robot, "condition": condition, **evidence})
        _write(
            workspace / "study_evidence.json",
            {
                "artifact": str(canonical_path),
                "evidence": evidence,
                "probe_results": list(probe_results),
            },
        )
        return result, probe_results
    except Exception as exc:
        evidence = _with_experience_trace(
            _stage_evidence(
                client,
                stage="study",
                before=before,
                completed=False,
                error=exc,
            ),
            experience,
        )
        stage_log.append({"robot": robot, "condition": condition, **evidence})
        raise


def _run_reference_positive_control(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    suite: Mapping[str, Any],
    config: ExperimentConfig,
    hooks: PipelineHooks,
    output_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    driver_path = render_reference_driver(
        package,
        design,
        output_dir,
        renderer=hooks.reference_renderer,
    )
    if hooks.reference_runner is None:
        raw = _default_reference_run(
            package=package,
            design=design,
            capability_suite=suite,
            driver_path=driver_path,
            output_dir=output_dir / "validation",
            config=config,
            run_id=run_id,
        )
    else:
        raw = _call_supported(
            hooks.reference_runner,
            package=package,
            design=_copy(dict(design)),
            suite=_copy(dict(suite)),
            driver_path=driver_path,
            trusted_reference_driver=True,
            output_dir=output_dir / "validation",
            record_video=config.record_video,
            wall_timeout_s=config.worker_wall_timeout_s,
            run_id=run_id,
            attempt=0,
        )
    report = _copy(dict(raw))
    report.update(
        {
            "evaluation_role": "ivc_reference_positive_control",
            "reference_driver": str(driver_path),
            "passed": _reference_passed(report, video_required=config.record_video),
        }
    )
    _write(output_dir / "reference_positive_control.json", report)
    if not report["passed"]:
        raise IVCError("private reference positive control did not pass the complete IVC suite")
    return report


def run_experiment(
    mainline_root: str | Path,
    *,
    config: ExperimentConfig | Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    run_id: str | None = None,
    client: Any | None = None,
    producer_client: Any | None = None,
    evolution_client: Any | None = None,
    model_identity: Mapping[str, Any] | None = None,
    experience: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    hooks: PipelineHooks | None = None,
    check_self_containment: bool = True,
    skip_reference_calibration: bool = True,
    sealed_inputs_from: str | Path | None = None,
) -> dict[str, Any]:
    """Run TGCD/IVC, optional hidden reference diagnostics, and dynamic cells.

    Dynamic capability-v2 IVC is admitted by deterministic inline-suite audit.
    A caller may still opt into the historical reference-driver diagnostic by
    passing ``skip_reference_calibration=False``; it is not a mainline gate.
    """

    root = Path(mainline_root).resolve()
    if config is None:
        config = ExperimentConfig.from_path(config_path or root / DEFAULT_CONFIG_PATH)
    elif not isinstance(config, ExperimentConfig):
        config = ExperimentConfig.from_mapping(config)
    if client is not None and producer_client is not None and client is not producer_client:
        raise PipelineError("client and producer_client must refer to the same Producer client")
    selected_producer = producer_client if producer_client is not None else client
    selected_run_id = run_id or _new_run_id(config.experiment_id)
    experience_snapshot_id = config.experience_snapshot_id
    experience_source_run_id = config.experience_source_run_id
    if config.experience_declared:
        if config.experience_snapshot_input is not None:
            if experience is not None:
                raise PipelineError(
                    "experience is declared in the manifest and cannot be overridden"
                )
            experience = _copy(dict(config.experience_snapshot_input))
        elif config.experience_input:
            if experience is not None:
                raise PipelineError(
                    "experience is declared in the manifest and cannot be overridden"
                )
            experience = tuple(config.experience_input)
        elif experience is not None and bool(experience):
            raise PipelineError("the manifest requires empty Experience and cannot be overridden")
    if isinstance(experience, Mapping) and experience.get(
        "artifact_type"
    ) == "autoadapter_experience_snapshot":
        snapshot_id_value = experience.get("snapshot_id")
        source_run_id_value = experience.get("source_run_id")
        if isinstance(snapshot_id_value, str) and snapshot_id_value.strip():
            experience_snapshot_id = snapshot_id_value.strip()
        if isinstance(source_run_id_value, str) and source_run_id_value.strip():
            experience_source_run_id = source_run_id_value.strip()
    experience_source_ids = _experience_source_run_ids(experience)
    if experience_source_run_id is not None:
        experience_source_ids.add(experience_source_run_id)
    if selected_run_id in experience_source_ids:
        raise PipelineError("same-round Experience input is not permitted")
    selected_hooks = hooks or PipelineHooks()
    destination = (
        Path(output_dir).resolve()
        if output_dir is not None
        else root / "runs" / selected_run_id
    )
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
        "mainline_root": str(root),
        "robots": list(config.robots),
        "self_containment": self_containment,
        "environment": environment,
        "package_check_passed": True,
    }
    _write(destination / "package_check.json", package_check)

    if selected_producer is None:
        from autoadapter2.model_api import JsonModelClient, ModelConfig

        try:
            selected_producer = JsonModelClient(ModelConfig.from_env())
        except Exception as exc:
            raise PipelineError(f"real model client configuration failed: {exc}") from exc
    client = selected_producer
    if config.evolution_enabled:
        if evolution_client is None and config.evolution_model_manifest is not None:
            evolution_client = _clone_client_for_manifest(
                selected_producer, config.evolution_model_manifest
            )
            if evolution_client is None:
                raise PipelineError(
                    "evolution.model requires a cloneable Producer client or an explicit evolution_client"
                )
        if evolution_client is None:
            # Backward-compatible single-client behavior.
            evolution_client = selected_producer
    else:
        # Disabled Evolution must not construct or touch a terminal model client.
        evolution_client = None
    identity = _client_identity(selected_producer, model_identity)
    model_preflight = _validate_model_preflight(selected_producer, config.model_manifest)
    if model_preflight is not None:
        _write(destination / "model_preflight.json", model_preflight)
    evolution_model_preflight = None
    if config.evolution_enabled and config.evolution_model_manifest is not None:
        evolution_model_preflight = _validate_model_preflight(
            evolution_client, config.evolution_model_manifest
        )
        if evolution_model_preflight is not None:
            _write(destination / "evolution_model_preflight.json", evolution_model_preflight)
    if sealed_inputs_from is not None:
        raise PipelineError(
            "sealed TGCD/IVC reuse is incompatible with fresh per-cell design and validation"
        )
    stage_log: list[dict[str, Any]] = []
    sealed_input_provenance: dict[str, Any] | None = None
    references: dict[str, Any] = {}
    cell_reports: list[dict[str, Any]] = []
    for robot in config.robots:
        for condition in config.generation_conditions:
            cell_workspace = destination / "cells" / robot / condition
            cell_id = f"{robot}::{condition}"
            package = packages[robot]
            robot_experience = _public_experience(experience, robot)
            current_stage = "study"
            stage_before: int | None = None
            tgcd_events: list[dict[str, Any]] = []
            ivc_events: list[dict[str, Any]] = []
            completed_study: StudyResult | None = None
            study_probe_results: tuple[Mapping[str, Any], ...] = ()
            design: dict[str, Any] | None = None
            try:
                completed_study, study_probe_results = _run_study_phase(
                    package=package,
                    robot=robot,
                    condition=condition,
                    config=config,
                    client=client,
                    experience=robot_experience,
                    workspace=cell_workspace,
                    hooks=selected_hooks,
                    stage_log=stage_log,
                )

                current_stage = "tgcd"
                stage_before = _call_count(client)

                def record_tgcd_event(event: Mapping[str, Any]) -> None:
                    tgcd_events.append(_json_safe(dict(event)))

                design = _call_supported(
                    selected_hooks.tgcd_runner,
                    client,
                    package,
                    experience=robot_experience,
                    study=completed_study.output,
                    max_turns=int(config.phase_turn_budgets["tgcd"]),
                    probe_budget=config.probe_budget,
                    callback=record_tgcd_event,
                    artifact_path=cell_workspace / "design" / "capability_design.json",
                )
                design = _copy(
                    dict(
                        _call_supported(
                            selected_hooks.capability_design_validator,
                            design,
                            package,
                        )
                    )
                )
                design_path = cell_workspace / "design" / "capability_design.json"
                if not design_path.is_file():
                    write_capability_design(design_path, design)
                tgcd_evidence = _with_experience_trace(
                    _stage_evidence(
                        client,
                        stage="tgcd",
                        before=stage_before,
                        completed=True,
                    ),
                    robot_experience,
                )
                tgcd_evidence["artifact_trace"] = _copy(tgcd_events)
                stage_log.append(
                    {"robot": robot, "condition": condition, **tgcd_evidence}
                )
                _write(
                    cell_workspace / "design" / "tgcd_artifact_trace.json",
                    {"events": tgcd_events},
                )

                reference_box: dict[str, Any] = {}

                def positive_control_hook(
                    *,
                    capability_design: Mapping[str, Any],
                    validation_suite: Mapping[str, Any],
                ) -> Mapping[str, Any]:
                    report = _run_reference_positive_control(
                        package=package,
                        design=capability_design,
                        suite=validation_suite,
                        config=config,
                        hooks=selected_hooks,
                        output_dir=cell_workspace / "private" / "reference-positive-control",
                        run_id=selected_run_id,
                    )
                    reference_box["report"] = report
                    return report

                current_stage = "ivc"
                stage_before = _call_count(client)

                def record_ivc_event(event: Mapping[str, Any]) -> None:
                    ivc_events.append(_json_safe(dict(event)))

                capability_suite = _call_supported(
                    selected_hooks.ivc_runner,
                    client,
                    package=package,
                    design=_copy(dict(design)),
                    max_turns=int(config.phase_turn_budgets["ivc"]),
                    probe_budget=config.probe_budget,
                    callback=record_ivc_event,
                    reference_positive_control_hook=(
                        None if skip_reference_calibration else positive_control_hook
                    ),
                    artifact_path=(
                        cell_workspace
                        / "private"
                        / "capability_validation_suite.json"
                    ),
                )
                capability_suite = _copy(
                    dict(
                        _call_supported(
                            selected_hooks.capability_suite_validator,
                            capability_suite,
                            package=package,
                            design=design,
                        )
                    )
                )
                suite_path = (
                    cell_workspace
                    / "private"
                    / "capability_validation_suite.json"
                )
                if not suite_path.is_file():
                    write_private_suite(suite_path, capability_suite)
                ivc_trace_path = (
                    cell_workspace / "private" / "ivc_artifact_trace.json"
                )
                if skip_reference_calibration:
                    reference = {
                        "robot_configuration_id": robot,
                        "evaluation_role": "ivc_reference_positive_control",
                        "skipped": True,
                        "skip_reason": (
                            "dynamic inline-suite audit does not require an "
                            "independent reference-driver diagnostic"
                        ),
                        "passed": False,
                    }
                else:
                    if "report" not in reference_box:
                        positive_control_hook(
                            capability_design=design,
                            validation_suite=capability_suite,
                        )
                    reference = _copy(dict(reference_box["report"]))
                references[cell_id] = reference
                ivc_evidence = _stage_evidence(
                    client,
                    stage="ivc",
                    before=stage_before,
                    completed=True,
                )
                private_model_calls = ivc_evidence.pop("model_calls", [])
                _write(
                    ivc_trace_path,
                    {
                        "events": ivc_events,
                        "model_calls": private_model_calls,
                    },
                )
                ivc_evidence.update(
                    {
                        "experience_ids": [],
                        "candidate_driver_visible": False,
                        "compiled_capability_validation_case_count": len(
                            capability_suite.get("cases", [])
                        ),
                        "inline_suite_audit_passed": True,
                        "reference_positive_control_passed": bool(
                            reference.get("passed")
                        ),
                        "artifact_trace_summary": (
                            _private_artifact_event_summaries(ivc_events)
                        ),
                        "private_artifact_trace_path": str(ivc_trace_path),
                    }
                )
                stage_log.append(
                    {"robot": robot, "condition": condition, **ivc_evidence}
                )
            except Exception as exc:
                if isinstance(exc, OSError):
                    raise
                failed_stage = current_stage
                if current_stage in {"tgcd", "ivc"}:
                    failed_evidence = _stage_evidence(
                        client,
                        stage=current_stage,
                        before=stage_before,
                        completed=False,
                        error=exc,
                    )
                    wrapped_trace = _artifact_error_trace(exc)
                    if current_stage == "tgcd":
                        failed_evidence = _with_experience_trace(
                            failed_evidence,
                            robot_experience,
                        )
                        if wrapped_trace:
                            tgcd_events.append(
                                {
                                    "stage": "tgcd-failed",
                                    "trace": wrapped_trace,
                                }
                            )
                        failed_evidence["artifact_trace"] = _copy(tgcd_events)
                        _write(
                            cell_workspace
                            / "design"
                            / "tgcd_artifact_trace.json",
                            {"events": tgcd_events},
                        )
                    else:
                        ivc_trace_path = (
                            cell_workspace
                            / "private"
                            / "ivc_artifact_trace.json"
                        )
                        private_model_calls = failed_evidence.pop(
                            "model_calls", []
                        )
                        private_react_trace = failed_evidence.pop(
                            "react_trace", []
                        )
                        if not private_react_trace:
                            private_react_trace = wrapped_trace
                        private_probe_results = failed_evidence.pop(
                            "probe_results", []
                        )
                        error = failed_evidence.get("error")
                        if isinstance(error, Mapping):
                            failed_evidence["error"] = {
                                "type": str(error.get("type", "IVCError")),
                                "message": (
                                    "IVC failed; inspect the private artifact trace"
                                ),
                            }
                        _write(
                            ivc_trace_path,
                            {
                                "events": ivc_events,
                                "model_calls": private_model_calls,
                                "react_trace": private_react_trace,
                                "probe_results": private_probe_results,
                            },
                        )
                        failed_evidence.update(
                            {
                                "experience_ids": [],
                                "candidate_driver_visible": False,
                                "artifact_trace_summary": (
                                    _private_artifact_event_summaries(ivc_events)
                                ),
                                "private_react_trace_event_count": len(
                                    private_react_trace
                                ),
                                "private_artifact_trace_path": str(
                                    ivc_trace_path
                                ),
                            }
                        )
                    stage_log.append(
                        {
                            "robot": robot,
                            "condition": condition,
                            **failed_evidence,
                        }
                    )
                failure_detail = _failure_record(exc)
                if failed_stage == "ivc":
                    failure_detail["message"] = (
                        "IVC failed; inspect the private artifact trace"
                    )
                raw_failed_cell = _pre_driver_failure_cell(
                    package=package,
                    robot=robot,
                    condition=condition,
                    config=config,
                    identity=identity,
                    experience=robot_experience,
                    workspace=cell_workspace,
                    failed_stage=failed_stage,
                    failure_detail=failure_detail,
                    model_stage_log=stage_log,
                    completed_study=completed_study,
                    completed_probe_results=study_probe_results,
                    design=design,
                    hooks=selected_hooks,
                    evolution_client=evolution_client,
                    evolution_enabled=config.evolution_enabled,
                )
                failed_cell = build_cell_report(raw_failed_cell)
                failed_cell["frozen_driver_attempt_count"] = 0
                failed_cell["passed_capability_whitelist"] = []
                cell_reports.append(failed_cell)
                continue

            raw_cell = _run_cell(
                package=package,
                design=design,
                capability_suite=capability_suite,
                robot=robot,
                condition=condition,
                config=config,
                client=client,
                evolution_client=evolution_client,
                evolution_enabled=config.evolution_enabled,
                identity=identity,
                experience=robot_experience,
                workspace=cell_workspace,
                run_id=selected_run_id,
                hooks=selected_hooks,
                model_stage_log=stage_log,
                completed_study=completed_study,
                completed_probe_results=study_probe_results,
            )
            cell = build_cell_report(raw_cell)
            cell["frozen_driver_attempt_count"] = int(
                raw_cell.get("frozen_driver_attempt_count", 0)
            )
            cell["passed_capability_whitelist"] = _copy(
                list(raw_cell.get("passed_capability_whitelist", []))
            )
            cell_reports.append(cell)

    references_passed = bool(references) and all(
        bool(reference.get("passed")) for reference in references.values()
    )

    paired = build_paired_report(
        cell_reports,
        expected_robots=config.robots,
        expected_conditions=config.generation_conditions,
        run_id=selected_run_id,
    )
    review_queue: dict[str, Any] | None = None
    if config.evolution_enabled:
        review_queue = build_experience_review_queue(
            run_id=selected_run_id,
            experiment_id=config.experiment_id,
            expected_robots=config.robots,
            expected_conditions=config.generation_conditions,
            cells=cell_reports,
        )
        _write(destination / config.experience_review_queue_output, review_queue)
    all_cells_passed = bool(
        paired["summary"]["all_cells_final_capability_validation_passed"]
    )
    all_cells_completed = bool(paired["summary"]["all_cells_pipeline_completed"])
    all_task_demos_executed = bool(
        paired["summary"]["all_cells_task_demo_executed"]
    )
    all_task_demos_passed = bool(paired["summary"]["all_cells_task_demo_passed"])
    result = {
        "experiment_id": config.experiment_id,
        "code_version": __version__,
        "run_id": selected_run_id,
        "configuration": config.as_dict(),
        "package_check": package_check,
        "references": references,
        "sealed_input_provenance": sealed_input_provenance,
        "reference_calibration_skipped": skip_reference_calibration,
        "reference_calibration_passed": references_passed,
        "cells": cell_reports,
        "paired_report": paired,
        "pipeline_completed": all_cells_completed,
        "evolution_enabled": config.evolution_enabled,
        "cell_pipeline_completed": (
            review_queue["cell_pipeline_completed"]
            if review_queue is not None
            else all_cells_completed
        ),
        "expected_evolution_outcome_count": (
            review_queue["expected_evolution_outcome_count"]
            if review_queue is not None
            else 0
        ),
        "retained_evolution_outcome_count": (
            review_queue["retained_evolution_outcome_count"]
            if review_queue is not None
            else 0
        ),
        "all_evolution_outcomes_retained": (
            review_queue["all_evolution_outcomes_retained"]
            if review_queue is not None
            else True
        ),
        "reviewed_disposition_count": (
            review_queue["reviewed_disposition_count"]
            if review_queue is not None
            else 0
        ),
        "dispositions_complete": (
            review_queue["dispositions_complete"]
            if review_queue is not None
            else True
        ),
        "producer_model": _copy(identity),
        "evolution_model": (
            _client_identity(evolution_client, None)
            if evolution_client is not None
            else None
        ),
        "experience_input_ids": {
            robot: _experience_ids(_public_experience(experience, robot))
            for robot in config.robots
        },
        "experience_snapshot_id": experience_snapshot_id,
        "experience_source_run_id": experience_source_run_id,
        "experience_input_loaded": experience is not None,
        "dynamic_model_called": any(bool(cell["dynamic_model_called"]) for cell in cell_reports),
        "driver_generated_in_run": all(
            bool(cell["driver_generated_in_run"]) for cell in cell_reports
        ),
        "capability_validation_executed": all(
            bool(cell["capability_validation_executed"]) for cell in cell_reports
        ),
        "initial_capability_validation_passed": all(
            bool(cell["initial_capability_validation_passed"])
            for cell in cell_reports
        ),
        "final_capability_validation_passed": all_cells_passed,
        "task_demo_executed": all_task_demos_executed,
        "task_demo_passed": all_task_demos_passed,
        # Compatibility aliases describe Capability Validation only.
        "physical_validation_executed": all(
            bool(cell["capability_validation_executed"]) for cell in cell_reports
        ),
        "initial_validation_passed": all(
            bool(cell["initial_capability_validation_passed"])
            for cell in cell_reports
        ),
        "final_validation_passed": all_cells_passed,
        "success": all_cells_passed and all_cells_completed,
        "claim": (
            "configured experiment ended before all cells completed; named cell failures remain"
            if not all_cells_completed
            else (
                "driver-synthesis mainline succeeded; Task Demo results reported separately"
                if all_cells_passed
                else "configured experiment completed; named cell synthesis failures remain"
            )
        ),
        "stage_evidence": stage_log,
    }
    _write(destination / "experiment_report.json", result)
    return result


def success_claim(result: Mapping[str, Any]) -> bool:
    """Return the strict success claim used by the full CLI."""

    return (
        bool(result.get("success"))
        and bool(result.get("final_capability_validation_passed"))
        and bool(result.get("pipeline_completed"))
    )


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONDITIONS",
    "ExperimentConfig",
    "PipelineError",
    "PipelineHooks",
    "check_packages",
    "load_experiment_packages",
    "render_reference_driver",
    "run_experiment",
    "success_claim",
]
