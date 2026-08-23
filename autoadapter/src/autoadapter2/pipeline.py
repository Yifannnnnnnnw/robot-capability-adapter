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
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
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
from autoadapter2.validation_compiler import (
    TASK_DEMO_TASK_COUNT,
    run_ivc,
    sample_task_demo_suite,
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


class PipelineError(RuntimeError):
    """Raised when the experiment cannot be admitted or composed."""


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
                max_complete_driver_checks=int(
                    development.get("max_complete_driver_checks", 1)
                ),
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
        evolution_model_manifest = None
        if evolution_declared:
            enabled_value = evolution.get("enabled", True)
            if not isinstance(enabled_value, bool):
                raise PipelineError("evolution.enabled must be boolean")
            evolution_enabled = enabled_value
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
                    if key not in {"enabled", "model", "model_manifest"}
                }
                if actual_evolution != expected_evolution:
                    raise PipelineError(
                        "evolution must require each terminal cell and the canonical outcome field"
                    )
            else:
                allowed_disabled = {"enabled", "model", "model_manifest"}
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
            "max_driver_attempts_per_condition": self.max_driver_attempts_per_condition,
            "development_probe": {
                "max_requests_per_stage": self.probe_budget.max_requests,
                "max_complete_driver_checks": (
                    self.probe_budget.max_complete_driver_checks
                ),
                "wall_timeout_s_per_request": self.probe_budget.timeout_s,
                "max_output_chars_per_request": self.probe_budget.max_output_chars,
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
                }
                if self.evolution_model_manifest is not None:
                    result["evolution"]["model"] = _copy(
                        dict(self.evolution_model_manifest)
                    )
            else:
                result["evolution"] = {"enabled": False}
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


def _reuse_sealed_inputs(
    *,
    source_dir: str | Path,
    mainline_root: Path,
    destination: Path,
    config: ExperimentConfig,
    packages: Mapping[str, RobotPackage],
    hooks: PipelineHooks,
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    source = Path(source_dir).resolve()
    runs_root = (mainline_root / "runs").resolve()
    try:
        source.relative_to(runs_root)
    except ValueError as exc:
        raise PipelineError(
            "reused sealed inputs must come from the mainline runs directory"
        ) from exc
    if source == destination.resolve():
        raise PipelineError("sealed-input source and destination run must differ")

    report = _read_object(source / "experiment_report.json", label="source run report")
    source_run_id = report.get("run_id")
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        raise PipelineError("source run report lacks run_id")
    source_evidence = report.get("stage_evidence")
    if not isinstance(source_evidence, list):
        raise PipelineError("source run report lacks stage_evidence")

    designs: dict[str, Mapping[str, Any]] = {}
    capability_suites: dict[str, Mapping[str, Any]] = {}
    task_demo_suites: dict[str, Mapping[str, Any]] = {}
    reused_evidence: list[dict[str, Any]] = []
    for robot in config.robots:
        for stage in ("tgcd", "ivc"):
            evidence = next(
                (
                    item
                    for item in source_evidence
                    if isinstance(item, Mapping)
                    and item.get("robot") == robot
                    and item.get("stage") == stage
                    and item.get("completed") is True
                ),
                None,
            )
            if evidence is None:
                raise PipelineError(
                    f"source run lacks completed {stage} evidence for {robot!r}"
                )
            reused_evidence.append(
                {
                    **_copy(dict(evidence)),
                    "reused": True,
                    "reused_from_run_id": source_run_id,
                }
            )

        package = packages[robot]
        design_path = source / "designs" / robot / "capability_design.json"
        private_dir = source / "private" / robot
        capability_path = private_dir / "capability_validation_suite.json"
        task_demo_path = private_dir / "task_demo_suite.json"
        legacy_capability_path = private_dir / "private_case_pool.json"
        legacy_demo_path = private_dir / "private_validation_suite.json"
        design = dict(
            hooks.capability_design_validator(
                _read_object(design_path, label=f"{robot} capability design"),
                package,
            )
        )
        capability_source = (
            capability_path if capability_path.is_file() else legacy_capability_path
        )
        capability_raw = _read_object(
            capability_source, label=f"{robot} capability validation suite"
        )
        if capability_raw.get("artifact_type") == "private_validation_suite":
            capability_raw["artifact_type"] = "capability_validation_suite"
        capability_suite = dict(
            hooks.capability_suite_validator(
                capability_raw,
                package=package,
                design=design,
            )
        )
        task_demo_source = task_demo_path if task_demo_path.is_file() else legacy_demo_path
        task_demo_suite = _read_object(
            task_demo_source, label=f"{robot} Task Demo suite"
        )
        if task_demo_suite.get("artifact_type") == "private_validation_suite":
            task_demo_suite["artifact_type"] = "task_demo_suite"
        selection = task_demo_suite.get("selection")
        seed = selection.get("seed") if isinstance(selection, Mapping) else None
        if not isinstance(seed, str) or not seed:
            raise PipelineError(f"{robot} Task Demo suite lacks its selection seed")
        if sample_task_demo_suite(
            package=package,
            design=design,
            seed=seed,
        ) != task_demo_suite:
            raise PipelineError(
                f"{robot} Task Demo suite differs from its Task Library selection"
            )

        write_capability_design(
            destination / "designs" / robot / "capability_design.json", design
        )
        write_private_suite(
            destination / "private" / robot / "capability_validation_suite.json",
            capability_suite,
        )
        write_private_suite(
            destination / "private" / robot / "task_demo_suite.json",
            task_demo_suite,
        )
        designs[robot] = design
        capability_suites[robot] = capability_suite
        task_demo_suites[robot] = task_demo_suite

    provenance = {
        "reused": True,
        "source_run_id": source_run_id,
        "source_run_directory": str(source),
        "robots": list(config.robots),
    }
    _write(destination / "sealed_input_reuse.json", provenance)
    return designs, capability_suites, task_demo_suites, reused_evidence, provenance


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
    return [
        str(item["experience_id"])
        for item in experience
        if isinstance(item, Mapping)
        and isinstance(item.get("experience_id"), str)
        and item.get("experience_id")
    ]


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
        if "experience_id" in value or "source_run_id" in value:
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
        str(item.get("source_run_id"))
        for item in _flatten_experience_records(experience)
        if isinstance(item.get("source_run_id"), str)
        and item.get("source_run_id")
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
    capability_suite: Mapping[str, Any],
    task_demo_suite: Mapping[str, Any],
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
    sealed_task_demo_suite = _copy(dict(task_demo_suite))
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
        model_stage_log.append(
            {
                "robot": robot,
                "condition": condition,
                **_with_experience_trace(evidence, experience),
            }
        )
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
        model_stage_log.append(
            {
                "robot": robot,
                "condition": condition,
                **_with_experience_trace(evidence, experience),
            }
        )
        dynamic_model_called = True
        failure = {"stage": "study", **_failure_record(exc)}
        _write(
            workspace / "study_error.json",
            {"failure": failure, "evidence": evidence},
        )

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
            before: int | None = None
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
                        {
                            "robot": robot,
                            "condition": condition,
                            **_with_experience_trace(repair_evidence, experience),
                        }
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
                    {
                        "robot": robot,
                        "condition": condition,
                        **_with_experience_trace(evidence, experience),
                    }
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
    task_demo = _normalise_validation_report(
        {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "video_complete": not config.record_video,
            "skipped": True,
            "skip_reason": "capability validation did not pass",
            "trials": [],
            "video_manifest": [],
        },
        robot=robot,
        condition=condition,
        attempt=max(0, len(attempts) - 1),
        record_video=config.record_video,
        evaluation_role="task_demo",
    )
    if final_pass and current_driver is not None:
        task_demo_attempt = int(terminal_validation.get("attempt", 0))
        controller_before = _call_count(client)
        try:
            task_demo_raw = hooks.harness_runner(
                package=package,
                design=_copy(public_design),
                suite=_copy(sealed_task_demo_suite),
                driver_path=current_driver,
                condition=condition,
                output_dir=workspace / "task-demo",
                record_video=config.record_video,
                wall_timeout_s=config.worker_wall_timeout_s,
                run_id=run_id,
                attempt=task_demo_attempt,
                controller_client=client,
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
                    **_with_experience_trace(
                        _stage_evidence(
                            client,
                            stage="task_demo_controller",
                            before=controller_before,
                            completed=controller_stage_completed,
                        ),
                        experience,
                    ),
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
                    **_with_experience_trace(
                        _stage_evidence(
                            client,
                            stage="task_demo_controller",
                            before=controller_before,
                            completed=False,
                            error=exc,
                        ),
                        experience,
                    ),
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
        not final_pass or bool(task_demo.get("pipeline_completed"))
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
                str(task_id)
                for capability in capabilities
                for task_id in capability.get("covered_task_ids", [])
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
        "task_demo_trials": task_demo.get("trials", []),
        "task_demo_video_manifest": task_demo.get("video_manifest", []),
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
            "CapabilityValidation": _copy(terminal_validation),
            "TaskDemoController": next(
                (
                    item
                    for item in reversed(model_stage_log)
                    if item.get("stage") == "task_demo_controller"
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
            evolution = hooks.evolution_runner(terminal_evolution_client, raw_report)
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
    skip_reference_calibration: bool = False,
    sealed_inputs_from: str | Path | None = None,
) -> dict[str, Any]:
    """Run TGCD/IVC, optional hidden reference diagnostics, and dynamic cells."""

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
    stage_log: list[dict[str, Any]] = []
    designs: dict[str, Mapping[str, Any]] = {}
    capability_suites: dict[str, Mapping[str, Any]] = {}
    task_demo_suites: dict[str, Mapping[str, Any]] = {}
    sealed_input_provenance: dict[str, Any] | None = None
    if sealed_inputs_from is not None:
        (
            designs,
            capability_suites,
            task_demo_suites,
            reused_evidence,
            sealed_input_provenance,
        ) = _reuse_sealed_inputs(
            source_dir=sealed_inputs_from,
            mainline_root=root,
            destination=destination,
            config=config,
            packages=packages,
            hooks=selected_hooks,
        )
        stage_log.extend(reused_evidence)
    # Every robot completes TGCD and IVC before any condition receives a driver workspace.
    for robot in (() if sealed_inputs_from is not None else config.robots):
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
            evidence = _with_experience_trace(evidence, robot_experience)
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
            evidence = _with_experience_trace(
                evidence,
                _public_experience(experience, robot),
            )
            stage_log.append({"robot": robot, **evidence})
            failure = {
                "pipeline_completed": False,
                "dynamic_model_called": bool(stage_log),
                "driver_generated_in_run": False,
                "capability_validation_executed": False,
                "initial_capability_validation_passed": False,
                "final_capability_validation_passed": False,
                "task_demo_executed": False,
                "task_demo_passed": False,
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
            capability_suite = selected_hooks.ivc_runner(
                client,
                package=package,
                design=_copy(dict(design)),
            )
            capability_suite = _copy(dict(capability_suite))
            selection_seed = config.task_demo_seed_template.format(
                run_id=selected_run_id,
                robot_configuration_id=robot,
            )
            task_demo_suite = sample_task_demo_suite(
                package=package,
                design=design,
                seed=selection_seed,
            )
            evidence = _stage_evidence(client, stage="ivc", before=before, completed=True)
            evidence = _with_experience_trace(
                evidence,
                _public_experience(experience, robot),
            )
            evidence["compiled_capability_validation_case_count"] = len(
                capability_suite.get("cases", [])
            )
            evidence["selected_task_demo_task_count"] = TASK_DEMO_TASK_COUNT
            evidence["compiled_task_demo_case_count"] = len(
                task_demo_suite.get("cases", [])
            )
            stage_log.append({"robot": robot, **evidence})
            write_private_suite(
                robot_private_dir / "capability_validation_suite.json",
                capability_suite,
            )
            write_private_suite(
                robot_private_dir / "task_demo_suite.json", task_demo_suite
            )
            capability_suites[robot] = capability_suite
            task_demo_suites[robot] = task_demo_suite
        except Exception as exc:
            evidence = _stage_evidence(
                client,
                stage="ivc",
                before=locals().get("before"),
                completed=False,
                error=exc,
            )
            evidence = _with_experience_trace(
                evidence,
                _public_experience(experience, robot),
            )
            stage_log.append({"robot": robot, **evidence})
            failure = {
                "pipeline_completed": False,
                "dynamic_model_called": True,
                "driver_generated_in_run": False,
                "capability_validation_executed": False,
                "initial_capability_validation_passed": False,
                "final_capability_validation_passed": False,
                "task_demo_executed": False,
                "task_demo_passed": False,
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

    references: dict[str, Any] = {}
    if skip_reference_calibration:
        for robot in config.robots:
            reference = {
                "robot_configuration_id": robot,
                "reference_driver": None,
                "skipped": True,
                "skip_reason": "optional hidden reference diagnostics were skipped",
                "evaluation_role": "diagnostic_reference",
                "pipeline_completed": False,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_complete": False,
                "passed": False,
            }
            references[robot] = reference
            _write(destination / "references" / robot / "reference_report.json", reference)
    else:
        # Optional hidden diagnostics complete before STUDY and never gate dynamic cells.
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
                        capability_suite=capability_suites[robot],
                        driver_path=driver_path,
                        output_dir=reference_dir / "validation",
                        config=config,
                        run_id=selected_run_id,
                    )
                else:
                    reference = selected_hooks.reference_runner(
                        package=package,
                        design=_copy(dict(designs[robot])),
                        suite=_copy(dict(capability_suites[robot])),
                        driver_path=driver_path,
                        output_dir=reference_dir / "validation",
                        record_video=config.record_video,
                        wall_timeout_s=config.worker_wall_timeout_s,
                        run_id=selected_run_id,
                        attempt=0,
                    )
                reference = _copy(dict(reference))
                reference["evaluation_role"] = "diagnostic_reference"
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
                    "evaluation_role": "diagnostic_reference",
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

    cell_reports: list[dict[str, Any]] = []
    for robot in config.robots:
        for condition in config.generation_conditions:
            cell_workspace = destination / "cells" / robot / condition
            raw_cell = _run_cell(
                package=packages[robot],
                design=designs[robot],
                capability_suite=capability_suites[robot],
                task_demo_suite=task_demo_suites[robot],
                robot=robot,
                condition=condition,
                config=config,
                client=client,
                evolution_client=evolution_client,
                evolution_enabled=config.evolution_enabled,
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
