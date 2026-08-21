"""Serial, synthesis-only execution of one Experiment 1 B1 cell."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoadapter2.capability_design import validate_capability_design
from autoadapter2.driver_synthesis.generation import (
    build_public_generation_inputs,
    generate,
    study,
)
from autoadapter2.driver_synthesis.probe import ProbeBudget, run_probes
from autoadapter2.driver_synthesis.repair import repair_with_probes
from autoadapter2.harness.runner import run_private_suite
from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.model_api import JsonModelClient, ModelConfig
from autoadapter2.validation_compiler import validate_capability_validation_suite

from .fixed_bundles import validate_b1_fixed_bundle


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiment" / "experiment1"
AUTOADAPTER_ROOT = REPOSITORY_ROOT / "autoadapter"
AUTOADAPTER_SOURCE_ROOT = AUTOADAPTER_ROOT / "src"
EXPECTED_UNIT_COUNT = 210
MAX_ACCEPTED_ATTEMPTS = 3
ALLOWED_CONDITIONS = {"skeleton-assisted", "from-scratch"}
EXISTING_FIXED_ROUTES = {
    "robotstudio_so101": AUTOADAPTER_ROOT
    / "runs"
    / "route-so101-deepseek-20260821a",
    "unitree-go2-stock-12dof": AUTOADAPTER_ROOT
    / "runs"
    / "route-go2-deepseek-20260821a",
}
FORBIDDEN_RUN_FLAGS = (
    "run_task_demo",
    "run_high_level_controller",
    "run_evolution",
)


class B1RunError(RuntimeError):
    """Raised when one B1 cell cannot enter or follow the fixed route."""


@dataclass(frozen=True)
class FixedBundle:
    """The selected robot's fixed public Driver interface and private criteria."""

    design: Mapping[str, Any]
    suite: Mapping[str, Any]
    container_path: Path
    design_path: Path
    suite_path: Path
    fixed_capability_interface_id: str
    fixed_capability_pass_standard_id: str
    validation_suite_id: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_json_safe(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise B1RunError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B1RunError(f"{label} must contain one JSON object: {path}")
    return value


def _reference_json(
    source: Path, value: Any, *, field: str
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        raise B1RunError(f"{source}: {field} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        path = source.parent / path
    path = path.resolve()
    return path, _read_json(path, label=field)


def _string_list(value: Any, *, field: str, source: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        raise B1RunError(f"{source}: {field} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise B1RunError(f"{source}: {field} must contain non-empty strings")
    result = [str(item).strip() for item in value]
    if len(result) != len(set(result)):
        raise B1RunError(f"{source}: {field} must not contain duplicates")
    return result


def resolve_experiment_manifest(manifest_path: Path) -> dict[str, Any]:
    """Expand the external Experiment 1 recipe without using Benchmark lifecycle state."""

    manifest_path = manifest_path.resolve()
    recipe = _read_json(manifest_path, label="Experiment 1 manifest")
    if recipe.get("track") != "B1":
        raise B1RunError(f"{manifest_path}: track must be B1")
    for flag in FORBIDDEN_RUN_FLAGS:
        if recipe.get(flag) is not False:
            raise B1RunError(f"{manifest_path}: B1 requires {flag}=false")
    if recipe.get("experience_input") != "empty":
        raise B1RunError(f"{manifest_path}: B1 requires empty Experience input")

    protocol_path, protocol = _reference_json(
        manifest_path, recipe.get("protocol"), field="protocol"
    )
    if protocol.get("track") != "B1":
        raise B1RunError(f"{protocol_path}: protocol track must be B1")
    for flag in FORBIDDEN_RUN_FLAGS:
        if protocol.get(flag) is not False:
            raise B1RunError(f"{protocol_path}: B1 requires {flag}=false")
    if protocol.get("max_driver_attempts_per_condition") != MAX_ACCEPTED_ATTEMPTS:
        raise B1RunError(f"{protocol_path}: B1 must allow exactly three attempts")
    allowed = set(
        _string_list(
            protocol.get("allowed_generation_conditions"),
            field="allowed_generation_conditions",
            source=protocol_path,
        )
    )
    if allowed != ALLOWED_CONDITIONS:
        raise B1RunError(f"{protocol_path}: B1 generation conditions are incomplete")

    backbone_path, backbone_set = _reference_json(
        manifest_path, recipe.get("backbone_set"), field="backbone_set"
    )
    backbone_ids = _string_list(
        backbone_set.get("backbone_ids"), field="backbone_ids", source=backbone_path
    )
    replicate_path, replicate_set = _reference_json(
        manifest_path, recipe.get("replicate_set"), field="replicate_set"
    )
    replicate_ids = _string_list(
        replicate_set.get("replicate_ids"),
        field="replicate_ids",
        source=replicate_path,
    )
    seed_by_replicate: dict[str, int | None] = {}
    for item in replicate_set.get("seed_map", []):
        if isinstance(item, Mapping) and isinstance(item.get("replicate_id"), str):
            seed = item.get("seed")
            seed_by_replicate[str(item["replicate_id"])] = (
                seed
                if isinstance(seed, int) and not isinstance(seed, bool)
                else None
            )

    coverage = recipe.get("condition_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise B1RunError(f"{manifest_path}: condition_coverage must be a list")
    units: list[dict[str, Any]] = []
    robot_ids: list[str] = []
    for assignment in coverage:
        if not isinstance(assignment, Mapping):
            raise B1RunError(f"{manifest_path}: condition coverage must use objects")
        condition = assignment.get("condition")
        if condition not in allowed:
            raise B1RunError(f"{manifest_path}: unsupported condition {condition!r}")
        robot_path, robot_set = _reference_json(
            manifest_path,
            assignment.get("robot_set"),
            field="condition robot_set",
        )
        assignment_robots = _string_list(
            robot_set.get("robot_configuration_ids"),
            field="robot_configuration_ids",
            source=robot_path,
        )
        for robot_id in assignment_robots:
            if robot_id not in robot_ids:
                robot_ids.append(robot_id)
            for backbone_id in backbone_ids:
                for replicate_id in replicate_ids:
                    pair_id = f"b1::{robot_id}::{backbone_id}::{replicate_id}"
                    units.append(
                        {
                            "unit_id": f"{pair_id}::{condition}",
                            "condition_pair_id": pair_id,
                            "robot_configuration_id": robot_id,
                            "backbone_id": backbone_id,
                            "replicate_id": replicate_id,
                            "replicate_seed": seed_by_replicate.get(replicate_id),
                            "generation_condition": condition,
                        }
                    )

    unit_ids = [str(unit["unit_id"]) for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise B1RunError(f"{manifest_path}: duplicate B1 unit IDs")
    if len(units) != EXPECTED_UNIT_COUNT:
        raise B1RunError(
            f"{manifest_path}: expected {EXPECTED_UNIT_COUNT} B1 units, got {len(units)}"
        )
    if recipe.get("expected_generation_condition_replicates") != len(units):
        raise B1RunError(f"{manifest_path}: expected unit count does not match matrix")
    if recipe.get("maximum_submitted_driver_attempts") != (
        len(units) * MAX_ACCEPTED_ATTEMPTS
    ):
        raise B1RunError(f"{manifest_path}: maximum attempt count does not match matrix")
    runtime_values = recipe.get("backbone_runtime_configs", {})
    if not isinstance(runtime_values, Mapping):
        raise B1RunError(f"{manifest_path}: backbone_runtime_configs must be an object")
    runtime_paths: dict[str, str] = {}
    for backbone_id, value in runtime_values.items():
        if not isinstance(backbone_id, str) or not isinstance(value, str) or not value:
            raise B1RunError(
                f"{manifest_path}: invalid backbone_runtime_configs entry"
            )
        path = Path(value)
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise B1RunError(f"backbone runtime config does not exist: {path}")
        runtime_paths[backbone_id] = str(path)
    return {
        "experiment_id": recipe.get("experiment_id"),
        "authority_revision": recipe.get("authority_revision"),
        "manifest_path": str(manifest_path),
        "protocol_path": str(protocol_path),
        "protocol_id": protocol.get("protocol_id"),
        "protocol_version": protocol.get("protocol_version"),
        "robot_ids": robot_ids,
        "backbone_runtime_config_paths": runtime_paths,
        "units": units,
        "unit_count": len(units),
    }


def _fixed_container_path(manifest_path: Path) -> Path:
    recipe = _read_json(manifest_path, label="Experiment 1 manifest")
    value = recipe.get("fixed_validation_bundle_set")
    if value is None:
        value = recipe.get("fixed_driver_criteria_set")
    if not isinstance(value, str) or not value.strip():
        raise B1RunError(
            f"{manifest_path}: required fixed Driver-and-criteria path is missing"
        )
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.exists():
        raise B1RunError(f"fixed Driver-and-criteria path does not exist: {path}")
    return path


def _directory_bundle_paths(container: Path, robot_id: str) -> tuple[Path, Path]:
    candidates = (
        (
            container / robot_id / "capability_design.json",
            container / robot_id / "capability_validation_suite.json",
        ),
        (
            container / "designs" / robot_id / "capability_design.json",
            container / "private" / robot_id / "capability_validation_suite.json",
        ),
    )
    for design_path, suite_path in candidates:
        if design_path.is_file() and suite_path.is_file():
            return design_path.resolve(), suite_path.resolve()
    raise B1RunError(
        f"{container}: missing fixed Driver interface or validation criteria for {robot_id}"
    )


def _entry_path(entry: Mapping[str, Any], aliases: Sequence[str], *, label: str) -> str:
    for field in aliases:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise B1RunError(f"fixed bundle entry is missing {label} path")


def _bundle_paths(
    manifest_path: Path, robot_ids: Sequence[str]
) -> tuple[Path, dict[str, tuple[Path, Path]], dict[str, Mapping[str, Any]]]:
    container = _fixed_container_path(manifest_path)
    paths: dict[str, tuple[Path, Path]] = {}
    metadata: dict[str, Mapping[str, Any]] = {}
    if container.is_dir():
        for robot_id in robot_ids:
            paths[robot_id] = _directory_bundle_paths(container, robot_id)
            metadata[robot_id] = {}
        return container, paths, metadata

    document = _read_json(container, label="fixed Driver-and-criteria set")
    entries = document.get("robots")
    if not isinstance(entries, Mapping):
        raise B1RunError(f"{container}: fixed set must contain a robots object")
    missing = [robot_id for robot_id in robot_ids if robot_id not in entries]
    if missing:
        raise B1RunError(
            f"{container}: fixed set is missing robots: {', '.join(missing)}"
        )
    for robot_id in robot_ids:
        entry = entries[robot_id]
        if isinstance(entry, str):
            bundle_dir = Path(entry)
            if not bundle_dir.is_absolute():
                bundle_dir = container.parent / bundle_dir
            paths[robot_id] = _directory_bundle_paths(bundle_dir.resolve(), robot_id)
            metadata[robot_id] = {}
            continue
        if not isinstance(entry, Mapping):
            raise B1RunError(f"{container}: invalid fixed entry for {robot_id}")
        design_value = _entry_path(
            entry,
            ("capability_design", "capability_design_path", "driver_interface"),
            label="capability design",
        )
        suite_value = _entry_path(
            entry,
            (
                "capability_validation_suite",
                "capability_validation_suite_path",
                "validation_criteria",
            ),
            label="capability validation suite",
        )
        design_path = Path(design_value)
        suite_path = Path(suite_value)
        if not design_path.is_absolute():
            design_path = container.parent / design_path
        if not suite_path.is_absolute():
            suite_path = container.parent / suite_path
        design_path = design_path.resolve()
        suite_path = suite_path.resolve()
        if not design_path.is_file() or not suite_path.is_file():
            raise B1RunError(
                f"{container}: fixed paths are unavailable for {robot_id}"
            )
        paths[robot_id] = (design_path, suite_path)
        metadata[robot_id] = dict(entry)
    return container, paths, metadata


def load_fixed_bundle(
    manifest_path: Path,
    robot_ids: Sequence[str],
    robot_id: str,
    package: Any,
) -> FixedBundle:
    """Load and validate only fixed inputs; reference evidence is deliberately absent."""

    container, paths, metadata = _bundle_paths(manifest_path, robot_ids)
    design_path, suite_path = paths[robot_id]
    design_raw = _read_json(design_path, label=f"{robot_id} capability design")
    suite_raw = _read_json(suite_path, label=f"{robot_id} validation criteria")
    if design_raw.get("artifact_type") == "b1_fixed_capability_design":
        if suite_raw.get("artifact_type") != "b1_fixed_validation_suite":
            raise B1RunError(
                f"{suite_path}: B1 fixed design requires a B1 fixed validation suite"
            )
        design, suite = validate_b1_fixed_bundle(
            design_raw,
            suite_raw,
            package=package,
        )
    else:
        design = dict(validate_capability_design(design_raw, package))
        suite = dict(
            validate_capability_validation_suite(
                suite_raw,
                package=package,
                design=design,
            )
        )
    entry = metadata[robot_id]
    interface_id = (
        entry.get("fixed_capability_interface_id")
        or design.get("capability_design_id")
        or design.get("design_id")
        or str(design_path)
    )
    pass_standard_id = (
        entry.get("fixed_capability_pass_standard_id")
        or suite.get("pass_standard_id")
        or str(EXPERIMENT_ROOT / "B1_DRIVER_VALIDATION_CRITERIA.md")
    )
    suite_id = (
        entry.get("validation_suite_id")
        or suite.get("suite_id")
        or suite.get("artifact_id")
        or str(suite_path)
    )
    return FixedBundle(
        design=design,
        suite=suite,
        container_path=container,
        design_path=design_path,
        suite_path=suite_path,
        fixed_capability_interface_id=str(interface_id),
        fixed_capability_pass_standard_id=str(pass_standard_id),
        validation_suite_id=str(suite_id),
    )


def load_existing_fixed_route(
    _manifest_path: Path,
    _robot_ids: Sequence[str],
    robot_id: str,
    _package: Any,
) -> FixedBundle:
    """Reuse the already executed fixed package route for an explicit canary mode."""

    route_root = EXISTING_FIXED_ROUTES.get(robot_id)
    if route_root is None:
        raise B1RunError(f"no existing fixed route is recorded for {robot_id}")
    design_path = route_root / "fixed_route_design.json"
    suite_path = route_root / "private" / "fixed_route_suite.json"
    design = _read_json(design_path, label=f"{robot_id} existing route design")
    suite = _read_json(suite_path, label=f"{robot_id} existing route suite")
    if design.get("robot_configuration_id") != robot_id:
        raise B1RunError(f"{design_path}: robot identity mismatch")
    if suite.get("robot_configuration_id") != robot_id:
        raise B1RunError(f"{suite_path}: robot identity mismatch")
    capabilities = design.get("capabilities")
    cases = suite.get("cases")
    if not isinstance(capabilities, list) or not isinstance(cases, list) or not cases:
        raise B1RunError(f"existing fixed route is incomplete for {robot_id}")
    # The SO-101 route predates the validation_contract projection used by the
    # current Harness. Project the already fixed suite clauses into the public
    # design in memory; no route input or suite criterion is regenerated.
    projected = copy.deepcopy(design)
    for capability in projected["capabilities"]:
        if capability.get("validation_contract"):
            continue
        clauses = []
        for case in cases:
            if case.get("capability_id") != capability.get("capability_id"):
                continue
            criterion = case.get("criterion")
            if not isinstance(criterion, Mapping):
                continue
            clauses.append(
                {
                    "case_role": case.get("case_role", "route_shakedown"),
                    "selection_rationale": "Projected from the already fixed route suite.",
                    "source_task_id": case.get("task_id"),
                    "source_clause_id": case.get("source_clause_id"),
                    **copy.deepcopy(dict(criterion)),
                }
            )
        if not clauses:
            raise B1RunError(
                f"existing fixed route lacks criteria for {capability.get('capability_id')}"
            )
        capability["validation_contract"] = clauses
    return FixedBundle(
        design=projected,
        suite=suite,
        container_path=route_root,
        design_path=design_path,
        suite_path=suite_path,
        fixed_capability_interface_id=f"existing-route::{robot_id}",
        fixed_capability_pass_standard_id=f"existing-route-criteria::{robot_id}",
        validation_suite_id=f"existing-route-suite::{robot_id}",
    )


def _failure(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:2000]}


def _native_calls(client: Any) -> list[Mapping[str, Any]]:
    calls = getattr(client, "calls", ())
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        return []
    return [item for item in calls if isinstance(item, Mapping)]


_PROVIDER_INFRASTRUCTURE_ERROR_TYPES = frozenset(
    {"http_error", "timeout", "transport_error"}
)


def _provider_infrastructure_blocker(
    calls: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether the failed stage ended on a provider transport request."""

    if not calls:
        return False
    final_call = calls[-1]
    error = final_call.get("error")
    if final_call.get("status") != "failed" or not isinstance(error, Mapping):
        return False
    return str(error.get("type", "")).strip().lower() in (
        _PROVIDER_INFRASTRUCTURE_ERROR_TYPES
    )


def _first_number(value: Mapping[str, Any], names: Sequence[str]) -> int | None:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _token_record(usage: Any) -> dict[str, Any]:
    raw = dict(usage) if isinstance(usage, Mapping) else {}
    prompt_details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    prompt_details = dict(prompt_details) if isinstance(prompt_details, Mapping) else {}
    completion_details = (
        dict(completion_details) if isinstance(completion_details, Mapping) else {}
    )
    known = {
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
        "total_tokens",
    }
    other = {
        str(key): child
        for key, child in raw.items()
        if key not in known and "token" in str(key).lower()
    }
    cache_hit = _first_number(
        raw, ("prompt_cache_hit_tokens", "cache_read_input_tokens", "cached_tokens")
    ) or _first_number(prompt_details, ("cached_tokens",))
    cache_miss = _first_number(raw, ("prompt_cache_miss_tokens",))
    input_tokens = _first_number(raw, ("prompt_tokens", "input_tokens"))
    if input_tokens is None and (cache_hit is not None or cache_miss is not None):
        input_tokens = int(cache_hit or 0) + int(cache_miss or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": _first_number(raw, ("completion_tokens", "output_tokens")),
        "cache_read_tokens": cache_hit,
        "cache_write_tokens": _first_number(raw, ("cache_write_input_tokens",)),
        "input_cache_hit_tokens": cache_hit,
        "input_cache_miss_tokens": cache_miss,
        "reasoning_tokens": _first_number(
            raw, ("reasoning_tokens",)
        )
        or _first_number(completion_details, ("reasoning_tokens",)),
        "other_provider_token_categories": other,
        "provider_usage": raw,
    }


class RecordingClient:
    """Record every client invocation visible at the experiment boundary."""

    def __init__(
        self,
        delegate: Any,
        on_update: Callable[[], None],
        price_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        self._delegate = delegate
        self._on_update = on_update
        self.calls: list[dict[str, Any]] = []
        self.target_attempt: int | None = None
        self._model_turns = 0
        self._price_snapshot = dict(price_snapshot or {})
        self._native_call_indices: dict[int, int] = {}

    @property
    def config(self) -> Any:
        return getattr(self._delegate, "config", None)

    def set_target_attempt(self, attempt: int | None) -> None:
        self.target_attempt = attempt

    def __getattr__(self, name: str) -> Any:
        if name == "generate_tool_turn" and callable(
            getattr(self._delegate, "generate_tool_turn", None)
        ):
            return self._generate_tool_turn
        return getattr(self._delegate, name)

    def _recorded_calls(
        self,
        *,
        stage: str,
        started_utc: str,
        started_monotonic: float,
        native_before: int,
        error: BaseException | None,
    ) -> None:
        native_after = _native_calls(self._delegate)
        native_records = [dict(item) for item in native_after[native_before:]]
        if not native_records:
            native_records = [{}]
        total_elapsed = max(0.0, time.monotonic() - started_monotonic)
        if error is None:
            self._model_turns += 1
        config = self.config
        message = str(error) if error is not None else ""
        status_match = re.search(r"HTTP\s+(\d{3})", message)
        completed_offset = len(native_records) - 1 if error is None else None
        for offset, native in enumerate(native_records):
            outer_call_index = len(self.calls) + 1
            native_call_index = native.get("call_index")
            if isinstance(native_call_index, int) and not isinstance(
                native_call_index, bool
            ):
                self._native_call_indices[native_call_index] = outer_call_index
            native_retry_of = native.get("retry_of_call_index")
            retry_of = (
                self._native_call_indices.get(native_retry_of)
                if isinstance(native_retry_of, int)
                and not isinstance(native_retry_of, bool)
                else None
            )
            retry_index = native.get("retry_index")
            if not isinstance(retry_index, int) or isinstance(retry_index, bool):
                retry_index = 0
            elapsed = native.get("elapsed_s")
            if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
                elapsed = total_elapsed
            http_status = native.get("http_status")
            if not isinstance(http_status, int) or isinstance(http_status, bool):
                http_status = int(status_match.group(1)) if status_match else None
            completed_turn = offset == completed_offset
            native_error = native.get("error")
            if completed_turn:
                call_error = None
            elif isinstance(native_error, Mapping):
                call_error = copy.deepcopy(dict(native_error))
            elif error is not None:
                call_error = _failure(error)
            else:
                call_error = {
                    "type": str(native.get("status") or "provider_error"),
                    "message": "physical provider request did not complete a model turn",
                }
            usage = _token_record(native.get("usage"))
            cost = _call_cost(usage, self._price_snapshot)
            self.calls.append(
                {
                    "call_index": outer_call_index,
                    "model_turn_index": (
                        self._model_turns if completed_turn else None
                    ),
                    "stage": stage,
                    "target_attempt": self.target_attempt,
                    "retry_of": retry_of,
                    "retry_index": retry_index,
                    "provider_request_id": native.get("provider_request_id")
                    or native.get("request_id"),
                    "utc_started_at": native.get("started_at_utc") or started_utc,
                    "utc_finished_at": native.get("ended_at_utc") or _utc_now(),
                    "elapsed_s": max(0.0, float(elapsed)),
                    "requested_model": native.get("requested_model")
                    or getattr(config, "model", None),
                    "returned_model": native.get("returned_model"),
                    "provider": native.get("provider")
                    or getattr(config, "provider", None),
                    "transport": native.get("api_protocol")
                    or getattr(config, "api_protocol", None),
                    "http_status": http_status,
                    "status": "succeeded" if completed_turn else "failed",
                    "error": call_error,
                    "tokens": usage,
                    "per_call_cost": cost,
                    "provider_record": native,
                }
            )
            self._on_update()

    def _invoke(self, method_name: str, *, stage: str, arguments: Mapping[str, Any]) -> Any:
        started_utc = _utc_now()
        started_monotonic = time.monotonic()
        native_before = len(_native_calls(self._delegate))
        method = getattr(self._delegate, method_name)
        try:
            result = method(stage=stage, **dict(arguments))
        except Exception as exc:
            self._recorded_calls(
                stage=stage,
                started_utc=started_utc,
                started_monotonic=started_monotonic,
                native_before=native_before,
                error=exc,
            )
            raise
        self._recorded_calls(
            stage=stage,
            started_utc=started_utc,
            started_monotonic=started_monotonic,
            native_before=native_before,
            error=None,
        )
        return result

    def generate_json(
        self, *, stage: str, prompt: str, inputs: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._invoke(
            "generate_json",
            stage=stage,
            arguments={"prompt": prompt, "inputs": inputs},
        )

    def _generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Any:
        return self._invoke(
            "generate_tool_turn",
            stage=stage,
            arguments={
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
            },
        )


def _call_cost(
    tokens: Mapping[str, Any], price_snapshot: Mapping[str, Any]
) -> float | None:
    hit = tokens.get("input_cache_hit_tokens")
    miss = tokens.get("input_cache_miss_tokens")
    output = tokens.get("output_tokens")
    rates = (
        price_snapshot.get("input_cache_hit"),
        price_snapshot.get("input_cache_miss"),
        price_snapshot.get("output"),
    )
    if not all(isinstance(value, (int, float)) for value in (hit, miss, output, *rates)):
        return None
    return (
        float(hit) * float(rates[0])
        + float(miss) * float(rates[1])
        + float(output) * float(rates[2])
    ) / 1_000_000.0


def _runtime_config_for_unit(
    resolved: Mapping[str, Any], unit: Mapping[str, Any], *, required: bool
) -> tuple[dict[str, Any] | None, Path | None]:
    values = resolved.get("backbone_runtime_config_paths", {})
    path_value = values.get(unit["backbone_id"]) if isinstance(values, Mapping) else None
    if not isinstance(path_value, str):
        if required:
            raise B1RunError(
                f"no pinned runtime config for backbone {unit['backbone_id']}"
            )
        return None, None
    path = Path(path_value).resolve()
    config = _read_json(path, label=f"{unit['backbone_id']} runtime config")
    if config.get("backbone_id") != unit["backbone_id"]:
        raise B1RunError(f"{path}: backbone_id does not match selected unit")
    return config, path


def _validated_runtime_model_config(pinned: Mapping[str, Any]) -> ModelConfig:
    runtime = ModelConfig.from_env()
    settings = pinned.get("inference_settings")
    if not isinstance(settings, Mapping):
        raise B1RunError("pinned runtime config lacks inference_settings")
    expected = {
        "model": pinned.get("exact_model_id"),
        "api_protocol": pinned.get("transport"),
        "base_url": str(pinned.get("endpoint_base_url", "")).rstrip("/"),
        "thinking": settings.get("thinking"),
        "max_tokens": settings.get("max_tokens"),
        "tool_history_mode": settings.get("tool_history_mode"),
        "history_char_budget": settings.get("history_char_budget"),
        "timeout_s": settings.get("timeout_s"),
    }
    actual = {
        "model": runtime.model,
        "api_protocol": runtime.api_protocol,
        "base_url": runtime.base_url.rstrip("/"),
        "thinking": runtime.thinking,
        "max_tokens": runtime.max_tokens,
        "tool_history_mode": runtime.tool_history_mode,
        "history_char_budget": runtime.history_char_budget,
        "timeout_s": runtime.timeout_s,
    }
    mismatches = [field for field in expected if expected[field] != actual[field]]
    if settings.get("temperature") != 0.0:
        mismatches.append("temperature")
    endpoint_path = pinned.get("endpoint_path")
    if not isinstance(endpoint_path, str) or not runtime.endpoint_url.endswith(endpoint_path):
        mismatches.append("endpoint_path")
    if mismatches:
        raise B1RunError(
            "runtime model environment differs from pinned backbone config: "
            + ", ".join(sorted(set(mismatches)))
        )
    return runtime


def _default_client_factory(unit: Mapping[str, Any]) -> Any:
    pinned = unit.get("_runtime_config")
    if not isinstance(pinned, Mapping):
        raise B1RunError("selected unit lacks its pinned runtime config")
    return JsonModelClient(_validated_runtime_model_config(pinned))


@dataclass(frozen=True)
class RunnerHooks:
    """Small seams for focused scripted checks; defaults are the real route."""

    manifest_resolver: Callable[[Path], Mapping[str, Any]] = resolve_experiment_manifest
    package_loader: Callable[..., Any] = load_indexed_robot_package
    bundle_loader: Callable[..., FixedBundle] = load_fixed_bundle
    client_factory: Callable[[Mapping[str, Any]], Any] = _default_client_factory
    study_runner: Callable[..., Any] = study
    probe_runner: Callable[..., Any] = run_probes
    generate_runner: Callable[..., Any] = generate
    public_inputs_builder: Callable[..., Mapping[str, Any]] = (
        build_public_generation_inputs
    )
    repair_runner: Callable[..., Any] = repair_with_probes
    harness_runner: Callable[..., Mapping[str, Any]] = run_private_suite


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _model_identity(
    client: Any,
    backbone_id: str,
    pinned: Mapping[str, Any] | None,
    pinned_path: Path | None,
) -> dict[str, Any]:
    config = getattr(client, "config", None)
    return {
        "backbone_id": backbone_id,
        "vendor": getattr(config, "provider", None),
        "exact_model_id": getattr(config, "model", None),
        "transport": getattr(config, "api_protocol", None),
        "base_url": getattr(config, "base_url", None),
        "inference_settings": {
            "temperature": 0.0,
            "thinking": getattr(config, "thinking", None),
            "tool_history_mode": getattr(config, "tool_history_mode", None),
        },
        "token_limit": getattr(config, "max_tokens", None),
        "timeout_s": getattr(config, "timeout_s", None),
        "provider_seed_applied": None,
        "provider_model_revision": pinned.get("provider_model_revision") if pinned else None,
        "runtime_config_path": str(pinned_path) if pinned_path else None,
        "price_snapshot": copy.deepcopy(pinned.get("price_snapshot")) if pinned else None,
    }


def _observable_error(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normal = str(key).lower()
            if normal in {"successful", "success", "passed", "ok"} and child is False:
                return True
            if normal == "timed_out" and child is True:
                return True
            if normal in {"spawn_error", "error"} and child is not None and child != "" and child is not False:
                return True
            if normal == "exit_code" and isinstance(child, int) and child != 0:
                return True
            if _observable_error(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_observable_error(child) for child in value)
    return False


def _decode_observation(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _evidence_traces(result_or_error: Any) -> dict[str, list[list[Mapping[str, Any]]]]:
    grouped: dict[str, list[list[Mapping[str, Any]]]] = {}
    evidences = []
    for name in ("prepare_call_evidence", "call_evidence"):
        evidence = getattr(result_or_error, name, None)
        if evidence is not None:
            evidences.append(evidence)
    direct_trace = getattr(result_or_error, "react_trace", ())
    if direct_trace:
        evidences.append(type("DirectTrace", (), {"stage": None, "react_trace": direct_trace})())
    for evidence in evidences:
        trace = getattr(evidence, "react_trace", ())
        stage = getattr(evidence, "stage", None)
        if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
            continue
        by_turn: dict[int, list[Mapping[str, Any]]] = {}
        for item in trace:
            if isinstance(item, Mapping) and isinstance(item.get("turn"), int):
                by_turn.setdefault(int(item["turn"]), []).append(item)
        if by_turn:
            key = str(stage) if stage else "*"
            grouped.setdefault(key, []).extend(
                by_turn[turn] for turn in sorted(by_turn)
            )
    return grouped


def _classify_turn(items: Sequence[Mapping[str, Any]]) -> tuple[str, list[str], Any, str | None]:
    tool_items = [item for item in items if isinstance(item.get("tool"), str)]
    tools = [str(item["tool"]) for item in tool_items]
    submission = next(
        (
            str(item["tool"])
            for item in tool_items
            if item["tool"] in {"submit_study", "submit_driver"}
            and item.get("ok") is True
        ),
        None,
    )
    outcomes = [
        {
            "tool": item.get("tool"),
            "ok": item.get("ok"),
            "observable_error": _observable_error(
                _decode_observation(item.get("observation"))
            ),
        }
        for item in items
        if "tool" in item
    ]
    has_error = any(
        item.get("ok") is False
        or _observable_error(_decode_observation(item.get("observation")))
        for item in items
        if "tool" in item
    )
    if submission is not None:
        action_type = "submit"
    elif has_error:
        action_type = "execute_error"
    elif any(tool in {"run_mujoco_probe", "check_driver"} for tool in tools):
        action_type = "execute_clean"
    else:
        action_type = "observe_or_plan"
    return action_type, tools, outcomes, submission


def _append_actions(
    record: dict[str, Any],
    *,
    calls_start: int,
    outer_stage: str,
    target_attempt: int | None,
    result_or_error: Any,
    accepted_submission: str | None,
    transition: str,
) -> None:
    new_calls = record["provider_calls"][calls_start:]
    successful = [call for call in new_calls if call.get("status") == "succeeded"]
    traces = _evidence_traces(result_or_error)
    trace_offsets: dict[str, int] = {}
    appended: list[dict[str, Any]] = []
    for call in successful:
        raw_stage = str(call.get("stage"))
        groups = traces.get(raw_stage, traces.get("*", []))
        offset = trace_offsets.get(raw_stage, 0)
        items = groups[offset] if offset < len(groups) else []
        trace_offsets[raw_stage] = offset + 1
        action_type, tools, outcome, submission = _classify_turn(items)
        appended.append(
            {
                "iteration": len(record["actions"]) + len(appended) + 1,
                "provider_call_index": call.get("call_index"),
                "model_turn_index": call.get("model_turn_index"),
                "stage": outer_stage,
                "target_attempt": target_attempt,
                "elapsed_s": call.get("elapsed_s"),
                "input_tokens": (
                    call.get("tokens", {}).get("input_tokens")
                    if isinstance(call.get("tokens"), Mapping)
                    else None
                ),
                "output_tokens": (
                    call.get("tokens", {}).get("output_tokens")
                    if isinstance(call.get("tokens"), Mapping)
                    else None
                ),
                "tool_name": tools[0] if len(tools) == 1 else None,
                "tool_names": tools,
                "tool_outcome": outcome,
                "action_type": action_type,
                "plot_action_type": (
                    "read_or_plan"
                    if action_type == "observe_or_plan"
                    else (
                        "execute_error"
                        if action_type == "execute_error"
                        else "execute_clean"
                    )
                ),
                "submission_event": submission,
                "stage_transition": None,
            }
        )
    if accepted_submission and appended and not any(
        action["submission_event"] for action in appended
    ):
        appended[-1]["action_type"] = "submit"
        appended[-1]["plot_action_type"] = "execute_clean"
        appended[-1]["submission_event"] = accepted_submission
    if appended:
        appended[-1]["stage_transition"] = transition
    record["actions"].extend(appended)


def _probe_elapsed(result: Any) -> float:
    total = 0.0
    for item in getattr(result, "probe_results", ()):
        if isinstance(item, Mapping) and isinstance(item.get("elapsed_s"), (int, float)):
            total += max(0.0, float(item["elapsed_s"]))
    return total


def _has_successful_physics_probe(results: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        item.get("exit_code") == 0
        and item.get("timed_out") is False
        and item.get("spawn_error") is None
        and isinstance(item.get("physics_steps"), int)
        and int(item["physics_steps"]) > 0
        for item in results
    )


def _provider_elapsed(calls: Sequence[Mapping[str, Any]]) -> float:
    return sum(
        max(0.0, float(call["elapsed_s"]))
        for call in calls
        if isinstance(call.get("elapsed_s"), (int, float))
    )


def _refresh_derived(record: dict[str, Any]) -> None:
    calls = record.get("provider_calls", [])
    actions = record.get("actions", [])
    attempts = record.get("attempts", [])
    token_fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    )
    token_totals: dict[str, int | None] = {}
    for field in token_fields:
        values = [
            call.get("tokens", {}).get(field)
            for call in calls
            if isinstance(call.get("tokens"), Mapping)
        ]
        known = [value for value in values if isinstance(value, int)]
        token_totals[field] = sum(known) if known else None
    other_tokens: dict[str, int] = {}
    for call in calls:
        tokens = call.get("tokens")
        other = tokens.get("other_provider_token_categories") if isinstance(tokens, Mapping) else None
        if isinstance(other, Mapping):
            for key, value in other.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    other_tokens[str(key)] = other_tokens.get(str(key), 0) + value
    boundaries: list[dict[str, Any]] = []
    previous_stage = None
    for action in actions:
        stage = action.get("stage")
        if stage != previous_stage:
            boundaries.append({"stage": stage, "iteration": action.get("iteration")})
            previous_stage = stage
    costs = [call.get("per_call_cost") for call in calls]
    known_costs = [float(value) for value in costs if isinstance(value, (int, float))]
    action_type_counts = {
        name: sum(1 for action in actions if action.get("action_type") == name)
        for name in (
            "observe_or_plan",
            "execute_clean",
            "execute_error",
            "submit",
        )
    }
    stacked_action_counts = {
        "read_or_plan": action_type_counts["observe_or_plan"],
        "execute_clean": (
            action_type_counts["execute_clean"] + action_type_counts["submit"]
        ),
        "execute_error": action_type_counts["execute_error"],
    }
    known_input = token_totals.get("input_tokens")
    known_output = token_totals.get("output_tokens")
    total_tokens = (
        int(known_input) + int(known_output)
        if isinstance(known_input, int) and isinstance(known_output, int)
        else None
    )
    iteration_count = len(actions)
    execution_error_count = action_type_counts["execute_error"]
    record["derived"] = {
        "submitted_attempt_count": sum(
            1 for attempt in attempts if attempt.get("submission_accepted") is True
        ),
        "iteration_count": iteration_count,
        "execution_error_count": execution_error_count,
        "provider_error_count": sum(
            1 for call in calls if call.get("status") == "failed"
        ),
        "retry_count": sum(
            1 for call in calls if isinstance(call.get("retry_index"), int) and call["retry_index"] > 0
        ),
        "token_totals": {
            **token_totals,
            "total_tokens": total_tokens,
            "other_provider_token_categories": other_tokens,
        },
        "action_type_counts": action_type_counts,
        "stacked_action_counts": stacked_action_counts,
        "total_model_cost": sum(known_costs) if known_costs else None,
        "stage_boundaries": boundaries,
        "visualization_summary": {
            "iteration_count": iteration_count,
            "execution_error_count": execution_error_count,
            "total_tokens": total_tokens,
            "stacked_action_counts": stacked_action_counts,
            "stage_boundaries": boundaries,
        },
    }


def _normalise_validation(
    raw: Mapping[str, Any], *, unit: Mapping[str, Any], attempt: int
) -> dict[str, Any]:
    result = copy.deepcopy(dict(raw))
    result.setdefault("robot_configuration_id", unit["robot_configuration_id"])
    result.setdefault("condition", unit["generation_condition"])
    result.setdefault("attempt", attempt)
    result.setdefault("pipeline_completed", False)
    result.setdefault("physical_validation_executed", False)
    result.setdefault("validation_passed", False)
    result.setdefault("video_complete", False)
    result.setdefault("trials", [])
    result.setdefault("video_manifest", [])
    result["validation_passed"] = bool(result["validation_passed"]) and bool(
        result["physical_validation_executed"]
    ) and bool(result["video_complete"])
    return result


def _validation_case_counts(
    suite: Mapping[str, Any], report: Mapping[str, Any]
) -> dict[str, int]:
    """Partition every expected suite case into pass, fail, or incomplete."""

    cases = suite.get("cases")
    if not isinstance(cases, list):
        cases = []
    expected: dict[str, int] = {}
    for case in cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
            continue
        repetitions = case.get("repetitions", 1)
        expected[str(case["case_id"])] = (
            int(repetitions)
            if isinstance(repetitions, int)
            and not isinstance(repetitions, bool)
            and repetitions > 0
            else 1
        )
    grouped: dict[str, list[Mapping[str, Any]]] = {
        case_id: [] for case_id in expected
    }
    trials = report.get("trials")
    if isinstance(trials, list):
        for trial in trials:
            if not isinstance(trial, Mapping):
                continue
            case_id = trial.get("case_id")
            if isinstance(case_id, str) and case_id in grouped:
                grouped[case_id].append(trial)
    counts = {"passed": 0, "failed": 0, "incomplete": 0, "total": len(expected)}
    for case_id, repetitions in expected.items():
        values = grouped[case_id]
        if len(values) != repetitions or any(
            trial.get("worker_completed") is not True for trial in values
        ):
            counts["incomplete"] += 1
        elif all(trial.get("trial_passed") is True for trial in values):
            counts["passed"] += 1
        else:
            counts["failed"] += 1
    return counts


def _select_unit(resolved: Mapping[str, Any], unit_id: str) -> dict[str, Any]:
    if resolved.get("unit_count") != EXPECTED_UNIT_COUNT:
        raise B1RunError(f"Experiment 1 manifest must resolve {EXPECTED_UNIT_COUNT} units")
    units = resolved.get("units")
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise B1RunError("resolved Experiment 1 manifest has no units")
    selected = [unit for unit in units if isinstance(unit, Mapping) and unit.get("unit_id") == unit_id]
    if len(selected) != 1:
        raise B1RunError(f"unit-id must select exactly one Experiment 1 cell: {unit_id}")
    unit = dict(selected[0])
    required = {
        "unit_id",
        "condition_pair_id",
        "robot_configuration_id",
        "backbone_id",
        "replicate_id",
        "generation_condition",
    }
    if required - set(unit):
        raise B1RunError("selected unit lacks required identities")
    if unit["generation_condition"] not in ALLOWED_CONDITIONS:
        raise B1RunError("selected unit has an unsupported generation condition")
    return unit


def run_single_cell(
    *,
    manifest_path: str | Path,
    unit_id: str,
    output_dir: str | Path,
    hooks: RunnerHooks = RunnerHooks(),
    probe_budget: ProbeBudget = ProbeBudget(),
    worker_wall_timeout_s: float = 120.0,
    use_existing_fixed_route: bool = False,
) -> dict[str, Any]:
    """Run exactly one serial B1 cell from STUDY to terminal validation."""

    manifest = Path(manifest_path).resolve()
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise B1RunError(f"cell output directory must be new or empty: {output}")
    resolved = dict(hooks.manifest_resolver(manifest))
    unit = _select_unit(resolved, unit_id)
    robot_ids = resolved.get("robot_ids")
    if not isinstance(robot_ids, Sequence) or isinstance(robot_ids, (str, bytes)):
        raise B1RunError("resolved manifest lacks the fixed robot cohort")
    active_bundle_loader = (
        load_existing_fixed_route
        if use_existing_fixed_route and hooks.bundle_loader is load_fixed_bundle
        else hooks.bundle_loader
    )
    if active_bundle_loader is load_fixed_bundle:
        # Give the declared sole start prerequisite precedence over package/runtime
        # diagnostics when the fixed path has not yet been connected.
        _fixed_container_path(manifest)
    pinned_runtime, pinned_runtime_path = _runtime_config_for_unit(
        resolved,
        unit,
        required=hooks.client_factory is _default_client_factory,
    )
    client_unit = {
        **unit,
        **({"_runtime_config": pinned_runtime} if pinned_runtime is not None else {}),
    }
    try:
        package = hooks.package_loader(
            AUTOADAPTER_ROOT, str(unit["robot_configuration_id"])
        )
        bundle = active_bundle_loader(
            manifest,
            tuple(str(robot_id) for robot_id in robot_ids),
            str(unit["robot_configuration_id"]),
            package,
        )
        base_client = hooks.client_factory(client_unit)
    except B1RunError:
        raise
    except Exception as exc:
        raise B1RunError(f"cannot prepare selected B1 cell: {type(exc).__name__}: {exc}") from exc

    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "workspace"
    workspace.mkdir()
    record_path = output / "cell_record.json"
    run_id = (
        f"{unit_id}::{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}::"
        f"{uuid.uuid4().hex[:8]}"
    )
    started_utc = _utc_now()
    started_monotonic = time.monotonic()
    record: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": resolved.get("experiment_id"),
        "track": "B1",
        "run_id": run_id,
        "identity": {
            **copy.deepcopy(unit),
            "cell_id": unit_id,
            "protocol_id": resolved.get("protocol_id"),
            "protocol_version": resolved.get("protocol_version"),
            "experiment_recipe": str(manifest),
            "authority_revision": resolved.get("authority_revision"),
            "git_commit": _git_commit(),
        },
        "model": _model_identity(
            base_client,
            str(unit["backbone_id"]),
            pinned_runtime,
            pinned_runtime_path,
        ),
        "fixed_inputs": {
            "mode": (
                "existing_fixed_route"
                if active_bundle_loader is load_existing_fixed_route
                else "manifest_fixed_set"
            ),
            "container_path": str(bundle.container_path),
            "capability_design_path": str(bundle.design_path),
            "capability_validation_suite_path": str(bundle.suite_path),
            "fixed_capability_interface_id": bundle.fixed_capability_interface_id,
            "fixed_capability_pass_standard_id": bundle.fixed_capability_pass_standard_id,
            "validation_suite_id": bundle.validation_suite_id,
        },
        "route": ["study", "generate_or_gen_algo", "conditional_repair", "validation"],
        "timing": {
            "utc_started_at": started_utc,
            "utc_finished_at": None,
            "total_wall_time_s": None,
            "time_to_attempt_0_verdict_s": None,
            "queue_time_s": 0.0,
            "active_time_s": None,
            "stage_wall_times_s": {},
            "model_wall_time_s": None,
            "tool_probe_wall_time_s": None,
            "harness_wall_time_s": None,
        },
        "stages": [],
        "attempts": [],
        "provider_calls": [],
        "actions": [],
        "evidence": {
            "cell_record_path": str(record_path),
            "workspace": str(workspace),
            "candidate_paths": [],
            "report_paths": [],
            "event_trace_path": str(record_path),
            "video_paths": [],
            "trusted_evidence_paths": [str(bundle.design_path), str(bundle.suite_path)],
        },
        "terminal_verdict": None,
        "derived": {},
    }

    def persist() -> None:
        _refresh_derived(record)
        _write_json(record_path, record)

    price_snapshot = (
        pinned_runtime.get("price_snapshot")
        if isinstance(pinned_runtime, Mapping)
        and isinstance(pinned_runtime.get("price_snapshot"), Mapping)
        else None
    )
    client = RecordingClient(base_client, persist, price_snapshot)
    record["provider_calls"] = client.calls
    persist()

    def execute_model_stage(
        stage_name: str,
        target_attempt: int | None,
        operation: Callable[[], Any],
        *,
        submission: str,
        transition: str,
        postprocess: Callable[[Any], None] | None = None,
    ) -> Any:
        calls_start = len(client.calls)
        stage_started = _utc_now()
        stage_monotonic = time.monotonic()
        stage_record: dict[str, Any] = {
            "stage": stage_name,
            "target_attempt": target_attempt,
            "utc_started_at": stage_started,
            "utc_finished_at": None,
            "elapsed_s": None,
            "model_service_elapsed_s": None,
            "tool_probe_elapsed_s": None,
            "completed": None,
        }
        record["stages"].append(stage_record)
        client.set_target_attempt(target_attempt)
        persist()
        try:
            result = operation()
            if postprocess is not None:
                postprocess(result)
        except Exception as exc:
            stage_record.update(
                {
                    "utc_finished_at": _utc_now(),
                    "elapsed_s": max(0.0, time.monotonic() - stage_monotonic),
                    "model_service_elapsed_s": _provider_elapsed(
                        client.calls[calls_start:]
                    ),
                    "tool_probe_elapsed_s": _probe_elapsed(exc),
                    "completed": False,
                    "error": _failure(exc),
                }
            )
            _append_actions(
                record,
                calls_start=calls_start,
                outer_stage=stage_name,
                target_attempt=target_attempt,
                result_or_error=exc,
                accepted_submission=None,
                transition="terminal_error",
            )
            persist()
            raise
        stage_record.update(
            {
                "utc_finished_at": _utc_now(),
                "elapsed_s": max(0.0, time.monotonic() - stage_monotonic),
                "model_service_elapsed_s": _provider_elapsed(
                    client.calls[calls_start:]
                ),
                "tool_probe_elapsed_s": _probe_elapsed(result),
                "completed": True,
                "submission": submission,
            }
        )
        _append_actions(
            record,
            calls_start=calls_start,
            outer_stage=stage_name,
            target_attempt=target_attempt,
            result_or_error=result,
            accepted_submission=submission,
            transition=transition,
        )
        persist()
        return result

    def execute_validation(attempt: int, driver_path: Path) -> tuple[dict[str, Any], float]:
        stage_started = _utc_now()
        stage_monotonic = time.monotonic()
        stage_record: dict[str, Any] = {
            "stage": "validation",
            "target_attempt": attempt,
            "utc_started_at": stage_started,
            "utc_finished_at": None,
            "elapsed_s": None,
            "completed": None,
        }
        record["stages"].append(stage_record)
        persist()
        error: Exception | None = None
        try:
            raw = hooks.harness_runner(
                package=package,
                design=copy.deepcopy(dict(bundle.design)),
                suite=copy.deepcopy(dict(bundle.suite)),
                driver_path=driver_path,
                condition=str(unit["generation_condition"]),
                output_dir=workspace / f"attempt-{attempt}" / "validation",
                record_video=True,
                wall_timeout_s=worker_wall_timeout_s,
                run_id=run_id,
                attempt=attempt,
            )
        except Exception as exc:
            error = exc
            raw = {
                "pipeline_completed": False,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_complete": False,
                "failure": _failure(exc),
                "trials": [],
                "video_manifest": [],
            }
        elapsed = max(0.0, time.monotonic() - stage_monotonic)
        validation = _normalise_validation(raw, unit=unit, attempt=attempt)
        validation["validation_case_counts"] = _validation_case_counts(
            bundle.suite, validation
        )
        report_path = workspace / f"attempt-{attempt}" / "validation_report.json"
        _write_json(report_path, validation)
        record["evidence"]["report_paths"].append(str(report_path))
        videos = validation.get("video_manifest", [])
        for video in videos if isinstance(videos, list) else []:
            if isinstance(video, Mapping):
                path = video.get("path") or video.get("video_path")
                if isinstance(path, str) and path:
                    record["evidence"]["video_paths"].append(path)
        stage_record.update(
            {
                "utc_finished_at": _utc_now(),
                "elapsed_s": elapsed,
                "completed": error is None,
                "validation_passed": bool(validation["validation_passed"]),
                **({"error": _failure(error)} if error is not None else {}),
            }
        )
        persist()
        return validation, elapsed

    def finish(
        *,
        passed: bool | None,
        stop_reason: str,
        attempt_index: int | None,
        failure: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        total = max(0.0, time.monotonic() - started_monotonic)
        stage_totals: dict[str, float] = {}
        for item in record["stages"]:
            elapsed = item.get("elapsed_s")
            if isinstance(elapsed, (int, float)):
                stage = str(item.get("stage"))
                stage_totals[stage] = stage_totals.get(stage, 0.0) + float(elapsed)
        record["timing"].update(
            {
                "utc_finished_at": _utc_now(),
                "total_wall_time_s": total,
                "active_time_s": total,
                "stage_wall_times_s": stage_totals,
                "model_wall_time_s": _provider_elapsed(record["provider_calls"]),
                "tool_probe_wall_time_s": sum(
                    float(item.get("tool_probe_elapsed_s") or 0.0)
                    for item in record["stages"]
                ),
                "harness_wall_time_s": stage_totals.get("validation", 0.0),
            }
        )
        submitted = sum(
            1
            for attempt in record["attempts"]
            if attempt.get("submission_accepted") is True
        )
        record["terminal_verdict"] = {
            "validation_passed": passed,
            "validation_executed": submitted > 0,
            "attempt_index": attempt_index,
            "submitted_attempt_count": submitted,
            "stop_reason": stop_reason,
            "no_valid_submission_reason": stop_reason if submitted == 0 else None,
            "terminal_failure_class": None if passed is True else stop_reason,
            "failure": copy.deepcopy(dict(failure)) if failure is not None else None,
        }
        persist()
        return copy.deepcopy(record)

    condition = str(unit["generation_condition"])
    generation_stage = "generate" if condition == "skeleton-assisted" else "gen_algo"
    study_probe_results: list[Mapping[str, Any]] = []

    def complete_study_probes(result: Any) -> None:
        existing = [
            dict(item)
            for item in getattr(result, "probe_results", ())
            if isinstance(item, Mapping)
        ]
        study_probe_results.extend(existing)
        requests = getattr(result, "probe_requests", ())
        if not existing and requests:
            produced = hooks.probe_runner(
                requests,
                package=package,
                workspace=workspace / "study-probes",
                condition=condition,
                budget=probe_budget,
                source_root=AUTOADAPTER_SOURCE_ROOT,
            )
            study_probe_results.extend(
                dict(item) for item in produced if isinstance(item, Mapping)
            )
        if requests and not _has_successful_physics_probe(study_probe_results):
            raise B1RunError("STUDY produced no successful real-physics probe")

    calls_before_study = len(client.calls)
    try:
        study_result = execute_model_stage(
            "study",
            None,
            lambda: hooks.study_runner(
                client,
                package,
                copy.deepcopy(dict(bundle.design)),
                condition=condition,
                experience=(),
                workspace=workspace / "study",
                probe_budget=probe_budget,
                source_root=AUTOADAPTER_SOURCE_ROOT,
            ),
            submission="submit_study",
            transition=generation_stage,
            postprocess=complete_study_probes,
        )
        if study_probe_results:
            record["stages"][-1]["tool_probe_elapsed_s"] = sum(
                max(0.0, float(item["elapsed_s"]))
                for item in study_probe_results
                if isinstance(item.get("elapsed_s"), (int, float))
            )
            persist()
    except Exception as exc:
        if study_probe_results:
            record["stages"][-1]["study_probe_results"] = _json_safe(
                study_probe_results
            )
            record["stages"][-1]["tool_probe_elapsed_s"] = sum(
                max(0.0, float(item["elapsed_s"]))
                for item in study_probe_results
                if isinstance(item.get("elapsed_s"), (int, float))
            )
            persist()
        provider_blocker = _provider_infrastructure_blocker(
            client.calls[calls_before_study:]
        )
        return finish(
            passed=None if provider_blocker else False,
            stop_reason=(
                "provider_infrastructure_blocker"
                if provider_blocker
                else "study_error"
            ),
            attempt_index=None,
            failure=_failure(exc),
        )

    current_source: str | None = None
    current_driver: Path | None = None
    previous_validation: dict[str, Any] | None = None
    for attempt_index in range(MAX_ACCEPTED_ATTEMPTS):
        attempt_started = _utc_now()
        attempt_monotonic = time.monotonic()
        attempt_record: dict[str, Any] = {
            "attempt_index": attempt_index,
            "utc_started_at": attempt_started,
            "utc_finished_at": None,
            "elapsed_s": None,
            "stage": generation_stage if attempt_index == 0 else "repair",
            "submission_accepted": False,
            "validation_verdict": None,
            "transition_or_stop": None,
            "model_wall_time_s": None,
            "tool_probe_wall_time_s": None,
            "validation_wall_time_s": None,
        }
        record["attempts"].append(attempt_record)
        persist()
        calls_before_attempt = len(client.calls)
        try:
            if attempt_index == 0:
                generated = execute_model_stage(
                    generation_stage,
                    attempt_index,
                    lambda: hooks.generate_runner(
                        client,
                        package,
                        copy.deepcopy(dict(bundle.design)),
                        study_result,
                        condition=condition,
                        workspace=workspace / f"attempt-{attempt_index}",
                        probe_results=tuple(study_probe_results),
                        experience=(),
                        probe_budget=probe_budget,
                        source_root=AUTOADAPTER_SOURCE_ROOT,
                    ),
                    submission="submit_driver",
                    transition=f"validation_{attempt_index}",
                )
            else:
                if current_source is None or previous_validation is None:
                    raise B1RunError("Repair requires the immediately preceding cell attempt")
                public_inputs = hooks.public_inputs_builder(
                    package,
                    copy.deepcopy(dict(bundle.design)),
                    condition=condition,
                    experience=(),
                    study_output=study_result.output,
                    probe_results=tuple(study_probe_results),
                )
                generated = execute_model_stage(
                    "repair",
                    attempt_index,
                    lambda: hooks.repair_runner(
                        client,
                        package=package,
                        previous_driver_source=current_source,
                        candidate_report=previous_validation,
                        media_manifest=previous_validation.get("video_manifest", []),
                        public_inputs=public_inputs,
                        condition=condition,
                        previous_attempt=attempt_index - 1,
                        workspace=workspace / f"attempt-{attempt_index}",
                        max_total_attempts=MAX_ACCEPTED_ATTEMPTS,
                        capability_methods=tuple(
                            str(capability["method_name"])
                            for capability in bundle.design["capabilities"]
                        ),
                        probe_budget=probe_budget,
                        source_root=AUTOADAPTER_SOURCE_ROOT,
                    ),
                    submission="submit_driver",
                    transition=f"validation_{attempt_index}",
                )
        except Exception as exc:
            provider_blocker = _provider_infrastructure_blocker(
                client.calls[calls_before_attempt:]
            )
            attempt_record.update(
                {
                    "utc_finished_at": _utc_now(),
                    "elapsed_s": max(0.0, time.monotonic() - attempt_monotonic),
                    "model_wall_time_s": _provider_elapsed(
                        client.calls[calls_before_attempt:]
                    ),
                    "transition_or_stop": (
                        "terminal_infrastructure_blocker"
                        if provider_blocker
                        else (
                            "terminal_generation_error"
                            if attempt_index == 0
                            else "terminal_repair_error"
                        )
                    ),
                    "failure": _failure(exc),
                }
            )
            persist()
            return finish(
                passed=None if provider_blocker else False,
                stop_reason=(
                    "provider_infrastructure_blocker"
                    if provider_blocker
                    else (
                        "generation_error"
                        if attempt_index == 0
                        else "repair_error"
                    )
                ),
                attempt_index=attempt_index,
                failure=_failure(exc),
            )

        current_source = str(generated.driver_source)
        current_driver = Path(generated.driver_path).resolve()
        attempt_record.update(
            {
                "submission_accepted": True,
                "candidate_path": str(current_driver),
                "source_audit_outcome": _json_safe(
                    getattr(generated, "source_audit", None)
                ),
                "development_probe_results": _json_safe(
                    getattr(generated, "probe_results", ())
                ),
            }
        )
        record["evidence"]["candidate_paths"].append(str(current_driver))
        persist()

        validation, validation_elapsed = execute_validation(
            attempt_index, current_driver
        )
        previous_validation = validation
        passed = bool(validation["validation_passed"])
        attempt_record.update(
            {
                "utc_finished_at": _utc_now(),
                "elapsed_s": max(0.0, time.monotonic() - attempt_monotonic),
                "model_wall_time_s": _provider_elapsed(
                    client.calls[calls_before_attempt:]
                ),
                "tool_probe_wall_time_s": _probe_elapsed(generated),
                "validation_wall_time_s": validation_elapsed,
                "validation_verdict": passed,
                "validation_report": validation,
                "validation_case_counts": validation["validation_case_counts"],
                "transition_or_stop": (
                    "passed"
                    if passed
                    else (
                        f"repair_{attempt_index + 1}"
                        if attempt_index + 1 < MAX_ACCEPTED_ATTEMPTS
                        else "maximum_attempts_reached"
                    )
                ),
            }
        )
        if attempt_index == 0:
            record["timing"]["time_to_attempt_0_verdict_s"] = max(
                0.0, time.monotonic() - started_monotonic
            )
        persist()
        if passed:
            return finish(
                passed=True,
                stop_reason="passed",
                attempt_index=attempt_index,
            )

    return finish(
        passed=False,
        stop_reason="maximum_attempts_reached",
        attempt_index=MAX_ACCEPTED_ATTEMPTS - 1,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run exactly one serial Experiment 1 B1 synthesis cell."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=EXPERIMENT_ROOT / "manifest.json",
        help="external 210-unit Experiment 1 manifest",
    )
    parser.add_argument("--unit-id", required=True, help="one exact B1 unit ID")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new per-cell output directory",
    )
    parser.add_argument(
        "--use-existing-fixed-route",
        action="store_true",
        help="reuse the already recorded SO-101/Go2 fixed route and criteria",
    )
    arguments = parser.parse_args(argv)
    try:
        record = run_single_cell(
            manifest_path=arguments.manifest,
            unit_id=arguments.unit_id,
            output_dir=arguments.output,
            use_existing_fixed_route=arguments.use_existing_fixed_route,
        )
    except B1RunError as exc:
        print(f"run_b1: {exc}", file=sys.stderr)
        return 2
    summary = {
        "unit_id": record["identity"]["unit_id"],
        "submitted_attempt_count": record["derived"]["submitted_attempt_count"],
        "validation_passed": record["terminal_verdict"]["validation_passed"],
        "cell_record": record["evidence"]["cell_record_path"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


__all__ = [
    "B1RunError",
    "FixedBundle",
    "RunnerHooks",
    "RecordingClient",
    "load_fixed_bundle",
    "load_existing_fixed_route",
    "resolve_experiment_manifest",
    "run_single_cell",
    "main",
]
