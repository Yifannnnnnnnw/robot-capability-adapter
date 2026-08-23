"""Small, direct orchestration boundary for the AA2-B2 formal cohort.

The existing B2 episode runner owns the persistent worker, ReCAP session, and
trusted Harness.  This module only resolves the fixed cohort, performs the
formal-dispatch gate, supplies parent-side provider inputs, and records one
secret-free terminal result for one planned unit.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .readiness import validate_readiness_evidence


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"
AUTHORITY_DOCUMENT_ID = "AA2-B2"
AUTHORITY_REVISION = "0.1.4"
EXPECTED_ROBOTS = ("robotstudio_so101", "unitree-go2-stock-12dof")
EXPECTED_MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
EXPECTED_REPLICATES = ("R1", "R2", "R3")
EXPECTED_INACTIVE_MODELS = ("M7",)
EXPECTED_TASK_IDS: dict[str, tuple[str, ...]] = {
    "robotstudio_so101": (
        "mw_push_to_goal",
        "mw_sweep_into_goal",
        "mw_pick_place",
        "mw_pick_place_wall",
        "mw_bin_picking",
    ),
    "unitree-go2-stock-12dof": (
        "GO2-T02",
        "GO2-T03",
        "GO2-T06",
        "GO2-T16",
        "GO2-T17",
    ),
}

_EXPECTED_SELECTIONS: dict[str, dict[str, str]] = {
    "robotstudio_so101": {
        "robot_configuration_id": "robotstudio_so101",
        "source_bundle_dir": (
            "experiment/experiment1a_generation/validation/fixed_validation_bundles/"
            "robotstudio_so101"
        ),
        "resolved_bundle_dir": (
            "experiment/experiment1b_use/validation/reference/resolved/"
            "robotstudio_so101"
        ),
        "package_root": "autoadapter/libraries/robots/robotstudio_so101/1.0.0",
        "driver_path": (
            "autoadapter/libraries/robots/robotstudio_so101/1.0.0/"
            "reference/fixed_capability_driver.py"
        ),
        "condition": "skeleton-assisted",
    },
    "unitree-go2-stock-12dof": {
        "robot_configuration_id": "unitree-go2-stock-12dof",
        "source_bundle_dir": (
            "experiment/experiment1a_generation/validation/fixed_validation_bundles/"
            "unitree-go2-stock-12dof"
        ),
        "resolved_bundle_dir": (
            "experiment/experiment1b_use/validation/reference/resolved/"
            "unitree-go2-stock-12dof"
        ),
        "package_root": "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0",
        "driver_path": (
            "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0/"
            "reference/fixed_capability_driver.py"
        ),
        "condition": "skeleton-assisted",
    },
}


class B2FormalError(ValueError):
    """Raised when the fixed B2 formal inputs are not mechanically valid."""


class FormalDispatchBlocked(B2FormalError):
    """Raised before any provider credential, model, or client is created."""


@dataclass(frozen=True)
class B2Unit:
    """One exact planned B2 episode."""

    unit_id: str
    robot_configuration_id: str
    task_id: str
    model_id: str
    replicate_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "unit_id": self.unit_id,
            "robot_configuration_id": self.robot_configuration_id,
            "task_id": self.task_id,
            "model_id": self.model_id,
            "replicate_id": self.replicate_id,
        }


@dataclass(frozen=True)
class ResolvedB2Manifest:
    """Validated manifest plus the mechanically enumerated cohort."""

    manifest_path: Path
    document: dict[str, Any]
    task_suite_path: Path
    provider_manifest_path: Path
    reference_selection_path: Path
    robots: tuple[str, ...]
    models: tuple[str, ...]
    replicates: tuple[str, ...]
    task_ids: Mapping[str, tuple[str, ...]]
    selections: Mapping[str, Mapping[str, Any]]
    provider_pins: Mapping[str, Mapping[str, Any]]
    provider_source_paths: Mapping[str, Path]
    readiness_evidence: Mapping[str, Any]
    provider_formal_ready: bool
    units: tuple[B2Unit, ...]

    @property
    def authority(self) -> dict[str, str]:
        return {"document_id": AUTHORITY_DOCUMENT_ID, "revision": AUTHORITY_REVISION}

    @property
    def formal_dispatch_enabled(self) -> bool:
        return self.document.get("formal_dispatch_enabled") is True

    @property
    def blockers(self) -> tuple[Any, ...]:
        raw = self.document.get("blockers")
        return tuple(raw) if isinstance(raw, list) else ()

    def unit(self, unit_id: str) -> B2Unit:
        for unit in self.units:
            if unit.unit_id == unit_id:
                return unit
        raise B2FormalError(f"unknown B2 planned unit: {unit_id}")

    def reference_paths(self, robot_configuration_id: str) -> tuple[Path, Path, Path]:
        """Return package root, selected fixed driver, and capability design."""

        selection = self.selections[robot_configuration_id]
        package_root = _repository_relative_path(selection["package_root"])
        driver_path = _repository_relative_path(selection["driver_path"])
        resolved_dir = _repository_relative_path(selection["resolved_bundle_dir"])
        return package_root, driver_path, resolved_dir / "capability_design.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise B2FormalError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B2FormalError(f"{label} must contain one JSON object")
    return value


def _required_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise B2FormalError(f"{label} must be a non-empty string")
    return value.strip()


def _relative_manifest_path(path: Path, value: Any, *, label: str) -> Path:
    raw = _required_string(value, label=label)
    relative = Path(raw)
    resolved = (
        relative.resolve() if relative.is_absolute() else (path.parent / relative).resolve()
    )
    if not resolved.is_file():
        raise B2FormalError(f"{label} does not resolve to a file: {resolved}")
    return resolved


def _repository_relative_path(value: Any) -> Path:
    raw = _required_string(value, label="reference selection path")
    relative = Path(raw)
    if relative.is_absolute():
        return relative.resolve()
    return (REPOSITORY_ROOT / relative).resolve()


def _check_authority(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping):
        raise B2FormalError(f"{label} has no authority object")
    if value.get("document_id") != AUTHORITY_DOCUMENT_ID:
        raise B2FormalError(f"{label} authority document is not {AUTHORITY_DOCUMENT_ID}")
    if value.get("revision") != AUTHORITY_REVISION:
        raise B2FormalError(f"{label} authority revision is not {AUTHORITY_REVISION}")


def _exact_strings(value: Any, expected: Sequence[str], *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or tuple(value) != tuple(expected):
        raise B2FormalError(f"{label} must be exactly {list(expected)!r}")
    if any(not isinstance(item, str) or not item for item in value):
        raise B2FormalError(f"{label} contains an invalid identifier")
    return tuple(value)


def _validate_formal_document(document: Mapping[str, Any]) -> tuple[str, ...]:
    if document.get("artifact_type") != "b2_recap_formal_manifest":
        raise B2FormalError("B2 formal manifest artifact_type is invalid")
    if document.get("schema_version") != "1.0":
        raise B2FormalError("B2 formal manifest schema_version is invalid")
    _check_authority(document.get("authority"), label="B2 formal manifest")
    robots = _exact_strings(document.get("robots"), EXPECTED_ROBOTS, label="robots")
    _exact_strings(document.get("models"), EXPECTED_MODELS, label="models")
    _exact_strings(
        document.get("inactive_historical_models"),
        EXPECTED_INACTIVE_MODELS,
        label="inactive_historical_models",
    )
    _exact_strings(document.get("replicates"), EXPECTED_REPLICATES, label="replicates")
    expected = document.get("expected_units")
    if expected != 210:
        raise B2FormalError("B2 formal manifest expected_units must be 210")
    if not isinstance(document.get("formal_dispatch_enabled"), bool):
        raise B2FormalError("formal_dispatch_enabled must be boolean")
    blockers = document.get("blockers")
    if not isinstance(blockers, list):
        raise B2FormalError("B2 formal manifest blockers must be a list")
    for index, blocker in enumerate(blockers):
        if not isinstance(blocker, Mapping):
            raise B2FormalError(f"B2 blocker {index} must be an object")
        _required_string(blocker.get("code"), label=f"B2 blocker {index}.code")
        _required_string(blocker.get("message"), label=f"B2 blocker {index}.message")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise B2FormalError("B2 formal manifest has no paths object")
    for key in ("task_suite", "provider_manifest", "reference_selection"):
        _required_string(paths.get(key), label=f"paths.{key}")
    if not isinstance(document.get("readiness_evidence"), Mapping):
        raise B2FormalError("B2 formal manifest has no readiness_evidence object")
    return robots


def _validate_task_suite(
    path: Path,
    *,
    robots: Sequence[str],
    replicates: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    suite = _read_object(path, label="B2 task suite")
    if suite.get("artifact_type") != "b2_recap_task_suite":
        raise B2FormalError("B2 task suite artifact_type is invalid")
    if suite.get("schema_version") != "1.0":
        raise B2FormalError("B2 task suite schema_version is invalid")
    _check_authority(suite.get("authority"), label="B2 task suite")
    if suite.get("task_count") != 10:
        raise B2FormalError("B2 task suite must contain exactly ten tasks")
    replicate_plan = suite.get("replicate_plan")
    if not isinstance(replicate_plan, Mapping) or tuple(
        replicate_plan.get("replicate_ids", ())
    ) != tuple(replicates):
        raise B2FormalError("B2 task suite replicate plan is not R1-R3")
    robot_suites = suite.get("robot_suites")
    if not isinstance(robot_suites, list) or tuple(
        item.get("robot_configuration_id")
        for item in robot_suites
        if isinstance(item, Mapping)
    ) != tuple(robots):
        raise B2FormalError("B2 task suite robot order/set is not the fixed pair")

    task_ids: dict[str, tuple[str, ...]] = {}
    for robot in robot_suites:
        if not isinstance(robot, Mapping):
            raise B2FormalError("B2 task suite contains an invalid robot suite")
        robot_id = _required_string(
            robot.get("robot_configuration_id"), label="B2 task-suite robot ID"
        )
        tasks = robot.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 5:
            raise B2FormalError(f"{robot_id} must have exactly five tasks")
        ids: list[str] = []
        for task in tasks:
            if not isinstance(task, Mapping):
                raise B2FormalError(f"{robot_id} contains an invalid task")
            task_id = _required_string(task.get("task_id"), label=f"{robot_id}.task_id")
            if task_id in ids:
                raise B2FormalError(f"{robot_id} contains duplicate task {task_id}")
            ids.append(task_id)
            inputs = task.get("replicate_inputs")
            if not isinstance(inputs, list) or tuple(
                item.get("replicate_id")
                for item in inputs
                if isinstance(item, Mapping)
            ) != tuple(replicates):
                raise B2FormalError(f"{robot_id}/{task_id} replicate inputs are not R1-R3")
            if len(inputs) != 3 or any(not isinstance(item, Mapping) for item in inputs):
                raise B2FormalError(f"{robot_id}/{task_id} has invalid replicate inputs")
            if any(
                item.get("reset_seed") is not None
                or item.get("reset_seed_applied") is not False
                for item in inputs
            ):
                raise B2FormalError(f"{robot_id}/{task_id} changes the fixed reset seed policy")
            resets = [item.get("reset") for item in inputs]
            scenes = [item.get("scene_entrypoint") for item in inputs]
            if any(value != resets[0] for value in resets) or any(
                value != scenes[0] for value in scenes
            ):
                raise B2FormalError(f"{robot_id}/{task_id} does not reuse one canonical reset/scene")
            rendering = task.get("rendering")
            if not isinstance(rendering, Mapping) or rendering.get("enabled") is not True:
                raise B2FormalError(f"{robot_id}/{task_id} must request video rendering")
            if rendering.get("continuous_episode_video_required") is not True:
                raise B2FormalError(f"{robot_id}/{task_id} must require continuous video")
        if tuple(ids) != EXPECTED_TASK_IDS[robot_id]:
            raise B2FormalError(f"{robot_id} task IDs are not the fixed B2 task block")
        task_ids[robot_id] = tuple(ids)
    if sum(len(value) for value in task_ids.values()) != 10:
        raise B2FormalError("B2 task suite task count is inconsistent")
    return task_ids


def _validate_provider_manifest(
    path: Path, *, models: Sequence[str]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Path], bool]:
    document = _read_object(path, label="B2 provider manifest")
    if document.get("artifact_type") != "b2_recap_provider_manifest":
        raise B2FormalError("B2 provider manifest artifact_type is invalid")
    if document.get("schema_version") != "1.0":
        raise B2FormalError("B2 provider manifest schema_version is invalid")
    _check_authority(document.get("authority"), label="B2 provider manifest")
    unresolved_prices = document.get("unresolved_price_backbones")
    if (
        not isinstance(unresolved_prices, list)
        or any(not isinstance(value, str) for value in unresolved_prices)
        or len(unresolved_prices) != len(set(unresolved_prices))
        or any(value not in models for value in unresolved_prices)
    ):
        raise B2FormalError("B2 provider unresolved_price_backbones is invalid")
    if not isinstance(document.get("diagnostic_only"), bool) or not isinstance(
        document.get("formal_dispatch_enabled"), bool
    ):
        raise B2FormalError("B2 provider dispatch flags must be boolean")
    runtime_configs = document.get("provider_runtime_configs")
    if not isinstance(runtime_configs, Mapping) or tuple(runtime_configs) != tuple(models):
        raise B2FormalError("B2 provider manifest model pins are not M1-M6,M8")
    common = document.get("common_transport_policy")
    if not isinstance(common, Mapping):
        raise B2FormalError("B2 provider manifest has no common transport policy")
    if common.get("transport") != "openai-compatible":
        raise B2FormalError("B2 provider transport is not openai-compatible")
    if common.get("endpoint_path") != "/chat/completions":
        raise B2FormalError("B2 provider endpoint path is not fixed")

    pins: dict[str, Mapping[str, Any]] = {}
    source_paths: dict[str, Path] = {}
    for model_id in models:
        raw_path = _required_string(
            runtime_configs.get(model_id), label=f"provider_runtime_configs.{model_id}"
        )
        relative = Path(raw_path)
        source_path = (
            relative.resolve() if relative.is_absolute() else (path.parent / relative).resolve()
        )
        source = _read_object(source_path, label=f"{model_id} provider pin")
        if source.get("backbone_id") != model_id:
            raise B2FormalError(f"{model_id} provider pin identity does not match")
        _required_string(source.get("exact_model_id"), label=f"{model_id}.exact_model_id")
        for field in (
            "vendor",
            "endpoint_base_url",
            "endpoint_path",
            "credential_env",
            "auth_header",
            "transport",
        ):
            _required_string(source.get(field), label=f"{model_id}.{field}")
        if not isinstance(source.get("auth_prefix"), str):
            raise B2FormalError(f"{model_id}.auth_prefix must be a string")
        if source.get("transport") != common.get("transport"):
            raise B2FormalError(f"{model_id} provider transport differs from the common pin")
        if source.get("endpoint_path") != common.get("endpoint_path"):
            raise B2FormalError(f"{model_id} provider endpoint path differs from the common pin")
        settings = source.get("inference_settings")
        if not isinstance(settings, Mapping):
            raise B2FormalError(f"{model_id} provider pin has no inference settings")
        if model_id == "M8":
            if (
                source.get("expected_returned_model_id")
                != source.get("exact_model_id")
                or source.get("provider_model_revision") is not None
                or source.get("upstream_revision_status")
                != "not_independently_verifiable"
                or source.get("context_limit_tokens") != 1_050_000
                or source.get("provider_max_output_tokens") != 128_000
                or source.get("limits_scope")
                != "OpenAI public model specification; company-gateway enforcement not independently verified"
                or source.get("limits_source")
                != "https://developers.openai.com/api/docs/models/gpt-5.6-sol"
            ):
                raise B2FormalError("M8 public model pin is invalid")
        else:
            _required_string(
                source.get("provider_model_revision"),
                label=f"{model_id}.provider_model_revision",
            )
        price = source.get("price_snapshot")
        if model_id not in unresolved_prices:
            if not isinstance(price, Mapping):
                raise B2FormalError(f"{model_id} provider pin has no dated price snapshot")
            for field in ("snapshot_date", "currency", "unit", "input_cache_miss", "output", "source"):
                value = price.get(field)
                if field in {"input_cache_miss", "output"}:
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) < 0:
                        raise B2FormalError(f"{model_id} price_snapshot.{field} is invalid")
                else:
                    _required_string(value, label=f"{model_id}.price_snapshot.{field}")
            if model_id == "M8":
                if (
                    price.get("input_cache_hit") != 0.5
                    or price.get("input_cache_miss") != 5.0
                    or price.get("output") != 30.0
                    or price.get("cost_basis")
                    != "public_standard_reference_estimate"
                    or price.get("pricing_scope")
                    != "OpenAI public Standard API reference; company-gateway billing not independently verified"
                ):
                    raise B2FormalError("M8 public base price pin is invalid")
                long_context = price.get("long_context")
                if not isinstance(long_context, Mapping) or dict(long_context) != {
                    "applies_when_input_tokens_gt": 272000,
                    "input_cache_hit": 1.0,
                    "input_cache_miss": 10.0,
                    "output": 45.0,
                }:
                    raise B2FormalError("M8 long-context public price pin is invalid")
        pins[model_id] = dict(source)
        source_paths[model_id] = source_path
    provider_formal_ready = (
        document.get("formal_dispatch_enabled") is True
        and document.get("diagnostic_only") is False
        and unresolved_prices == []
    )
    return pins, source_paths, provider_formal_ready


def _validate_reference_selection(path: Path, *, robots: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    document = _read_object(path, label="B2 reference selection")
    values = document.get("robots")
    if not isinstance(values, list) or tuple(
        item.get("robot_configuration_id")
        for item in values
        if isinstance(item, Mapping)
    ) != tuple(robots):
        raise B2FormalError("B2 reference selection robot order/set is not fixed")
    if len(values) != len(robots) or any(not isinstance(item, Mapping) for item in values):
        raise B2FormalError("B2 reference selection contains invalid robot entries")
    selections: dict[str, Mapping[str, Any]] = {}
    for item in values:
        robot_id = str(item.get("robot_configuration_id"))
        expected = _EXPECTED_SELECTIONS.get(robot_id)
        if expected is None or dict(item) != expected:
            raise B2FormalError(f"B2 reference selection changed the fixed {robot_id} pin")
        selections[robot_id] = dict(item)
    return selections


def _enumerate_units(
    *,
    robots: Sequence[str],
    models: Sequence[str],
    replicates: Sequence[str],
    task_ids: Mapping[str, Sequence[str]],
) -> tuple[B2Unit, ...]:
    units: list[B2Unit] = []
    for replicate_id in replicates:
        for robot_id in robots:
            for task_id in task_ids[robot_id]:
                for model_id in models:
                    units.append(
                        B2Unit(
                            unit_id=(
                                f"b2::{robot_id}::{task_id}::{model_id}::{replicate_id}"
                            ),
                            robot_configuration_id=robot_id,
                            task_id=task_id,
                            model_id=model_id,
                            replicate_id=replicate_id,
                        )
                    )
    if len(units) != 210 or len({unit.unit_id for unit in units}) != 210:
        raise B2FormalError("B2 formal cohort is not exactly 210 unique units")
    if any(
        sum(unit.model_id == model_id for unit in units) != 30
        for model_id in models
    ):
        raise B2FormalError("B2 formal cohort does not contain 30 units per model")
    if any(
        sum(
            unit.robot_configuration_id == robot_id and unit.model_id == model_id
            for unit in units
        )
        != 15
        for robot_id in robots
        for model_id in models
    ):
        raise B2FormalError("B2 formal cohort does not contain 15 units per robot/model")
    if any(
        sum(
            unit.robot_configuration_id == robot_id
            and unit.task_id == task_id
            and unit.model_id == model_id
            for unit in units
        )
        != 3
        for robot_id in robots
        for task_id in task_ids[robot_id]
        for model_id in models
    ):
        raise B2FormalError("B2 formal cohort does not contain 3 units per robot/task/model")
    return tuple(units)


def resolve_manifest(path: str | Path = DEFAULT_MANIFEST_PATH) -> ResolvedB2Manifest:
    """Read and mechanically resolve the complete fixed B2 cohort."""

    manifest_path = Path(path).resolve()
    document = _read_object(manifest_path, label="B2 formal manifest")
    robots = _validate_formal_document(document)
    models = _exact_strings(document.get("models"), EXPECTED_MODELS, label="models")
    replicates = _exact_strings(
        document.get("replicates"), EXPECTED_REPLICATES, label="replicates"
    )
    paths = document["paths"]
    task_suite_path = _relative_manifest_path(
        manifest_path, paths["task_suite"], label="paths.task_suite"
    )
    provider_manifest_path = _relative_manifest_path(
        manifest_path, paths["provider_manifest"], label="paths.provider_manifest"
    )
    reference_selection_path = _relative_manifest_path(
        manifest_path,
        paths["reference_selection"],
        label="paths.reference_selection",
    )
    task_ids = _validate_task_suite(
        task_suite_path, robots=robots, replicates=replicates
    )
    provider_pins, provider_source_paths, provider_formal_ready = _validate_provider_manifest(
        provider_manifest_path, models=models
    )
    selections = _validate_reference_selection(reference_selection_path, robots=robots)
    task_suite_document = _read_object(task_suite_path, label="B2 task suite")
    try:
        readiness_evidence = validate_readiness_evidence(
            manifest_path=manifest_path,
            manifest=document,
            task_suite=task_suite_document,
            selections=selections,
            repository_root=REPOSITORY_ROOT,
        )
    except ValueError as exc:
        raise B2FormalError(f"B2 readiness evidence is invalid: {exc}") from exc
    if document.get("formal_dispatch_enabled") is True and not provider_formal_ready:
        raise B2FormalError(
            "B2 formal manifest cannot be enabled while the provider manifest is paused"
        )
    units = _enumerate_units(
        robots=robots,
        models=models,
        replicates=replicates,
        task_ids=task_ids,
    )
    return ResolvedB2Manifest(
        manifest_path=manifest_path,
        document=document,
        task_suite_path=task_suite_path,
        provider_manifest_path=provider_manifest_path,
        reference_selection_path=reference_selection_path,
        robots=tuple(robots),
        models=tuple(models),
        replicates=tuple(replicates),
        task_ids={key: tuple(value) for key, value in task_ids.items()},
        selections=selections,
        provider_pins=provider_pins,
        provider_source_paths=provider_source_paths,
        readiness_evidence=readiness_evidence,
        provider_formal_ready=provider_formal_ready,
        units=units,
    )


def assert_dispatch_enabled(manifest: ResolvedB2Manifest) -> None:
    """Reject formal work while the versioned manifest is still paused."""

    if not manifest.provider_formal_ready:
        raise FormalDispatchBlocked(
            "B2 provider manifest is not enabled with complete dated price pins"
        )
    if not manifest.formal_dispatch_enabled:
        raise FormalDispatchBlocked(
            "B2 formal dispatch is disabled in the manifest"
        )
    if manifest.blockers:
        raise FormalDispatchBlocked(
            "B2 formal dispatch remains blocked: "
            + "; ".join(
                str(item.get("code", item)) if isinstance(item, Mapping) else str(item)
                for item in manifest.blockers
            )
        )


def terminal_path_for_unit(output_root: str | Path, unit_id: str) -> Path:
    safe = unit_id.replace("::", "__")
    return Path(output_root).resolve() / "terminals" / f"{safe}.json"


def episode_output_for_unit(output_root: str | Path, unit_id: str) -> Path:
    safe = unit_id.replace("::", "__")
    return Path(output_root).resolve() / "episodes" / safe


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    serialized = json.dumps(dict(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, path)


def _code_version() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "git_commit": result.stdout.strip() if result.returncode == 0 else None,
        "formal_runtime_path": str(Path(__file__).resolve()),
        "episode_runner_path": str(
            REPOSITORY_ROOT / "autoadapter/src/autoadapter2/b2/episode_runner.py"
        ),
    }


def _load_dotenv(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise B2FormalError(f"credential env file is absent: {path}") from exc
    values: dict[str, str] = {}
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise B2FormalError(f"invalid dotenv assignment at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in values:
            raise B2FormalError(f"invalid or duplicate dotenv key at line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _default_credential_loader(provider_pin: Mapping[str, Any], env_file: Path | None) -> str:
    selected = env_file or (REPOSITORY_ROOT / ".env.company-api")
    values = _load_dotenv(selected)
    key = _required_string(provider_pin.get("credential_env"), label="credential_env")
    credential = values.get(key, "")
    if not credential:
        raise B2FormalError(f"{key} is absent from the selected env file")
    return credential


def _provider_config(
    provider_pin: Mapping[str, Any], provider_manifest_path: Path
) -> Any:
    from autoadapter2.b2.model_client import B2ModelProviderConfig

    provider_manifest = _read_object(provider_manifest_path, label="B2 provider manifest")
    common = provider_manifest.get("common_transport_policy")
    if not isinstance(common, Mapping):
        raise B2FormalError("B2 provider common transport policy is invalid")
    settings = provider_pin.get("inference_settings")
    if not isinstance(settings, Mapping):
        raise B2FormalError("B2 provider inference settings are invalid")
    return B2ModelProviderConfig(
        provider=str(provider_pin.get("vendor") or "company").strip().lower(),
        model=_required_string(provider_pin.get("exact_model_id"), label="exact_model_id"),
        base_url=_required_string(
            provider_pin.get("endpoint_base_url"), label="endpoint_base_url"
        ).rstrip("/"),
        api_protocol=str(common.get("transport", "openai-compatible")),
        auth_header=_required_string(provider_pin.get("auth_header"), label="auth_header"),
        auth_prefix=str(provider_pin.get("auth_prefix", "")),
        thinking=settings.get("thinking"),
        timeout_s=float(common.get("timeout_s", settings.get("timeout_s", 120))),
        max_tokens=int(common.get("max_tokens", 4096)),
        history_char_budget=int(
            common.get("history_char_budget", settings.get("history_char_budget", 80_000))
        ),
    )


def _default_model_factory(provider_config: Any, credential: str) -> Any:
    from autoadapter2.b2.model_client import ReCAPJsonModelClient

    return ReCAPJsonModelClient(
        provider_config=provider_config,
        credential=credential,
    )


def _model_records(model: Any, name: str) -> list[dict[str, Any]]:
    records = getattr(model, name, ())
    if callable(records):
        records = records()
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return []
    return [dict(item) for item in records if isinstance(item, Mapping)]


def _call_cost_usd(
    call: Mapping[str, Any], price_snapshot: Mapping[str, Any]
) -> float | None:
    def numeric(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0.0
        )

    def first_numeric(*values: Any) -> float | None:
        for value in values:
            if numeric(value):
                return float(value)
        return None

    output_tokens = first_numeric(call.get("output_tokens"))
    total_input = first_numeric(call.get("input_tokens"))
    cache_hit = first_numeric(
        call.get("input_cache_hit_tokens"), call.get("cache_read_tokens")
    )
    cache_miss = first_numeric(call.get("input_cache_miss_tokens"))
    if output_tokens is None:
        return None
    if cache_hit is not None and cache_miss is not None:
        threshold_input = total_input if total_input is not None else cache_hit + cache_miss
    elif total_input is not None and cache_hit is not None:
        cache_miss = max(total_input - cache_hit, 0.0)
        threshold_input = total_input
    elif total_input is not None and cache_miss is not None:
        cache_hit = max(total_input - cache_miss, 0.0)
        threshold_input = total_input
    elif total_input is not None:
        cache_hit = 0.0
        cache_miss = total_input
        threshold_input = total_input
    else:
        return None

    rates: Mapping[str, Any] = price_snapshot
    long_context = price_snapshot.get("long_context")
    if isinstance(long_context, Mapping):
        threshold = long_context.get("applies_when_input_tokens_gt")
        if not numeric(threshold):
            return None
        if threshold_input > float(threshold):
            rates = long_context
    miss_rate = rates.get("input_cache_miss")
    hit_rate = rates.get("input_cache_hit")
    output_rate = rates.get("output")
    if not numeric(miss_rate) or not numeric(output_rate):
        return None
    if cache_hit > 0.0 and not numeric(hit_rate):
        return None
    effective_hit_rate = float(hit_rate) if numeric(hit_rate) else 0.0
    return (
        cache_miss * float(miss_rate)
        + cache_hit * effective_hit_rate
        + output_tokens * float(output_rate)
    ) / 1_000_000.0


def _provider_evidence(
    *,
    model: Any,
    provider_pin: Mapping[str, Any],
    provider_source_path: Path,
) -> dict[str, Any]:
    calls = _model_records(model, "provider_call_records")
    exchanges = _model_records(model, "provider_exchange_records")
    price = provider_pin.get("price_snapshot")
    price_snapshot = dict(price) if isinstance(price, Mapping) else {}
    costs = [_call_cost_usd(call, price_snapshot) for call in calls]
    known_costs = [cost for cost in costs if cost is not None]
    return {
        "provider_source_path": str(provider_source_path),
        "backbone_id": provider_pin.get("backbone_id"),
        "requested_model": provider_pin.get("exact_model_id"),
        "provider_model_revision": provider_pin.get("provider_model_revision"),
        "price_snapshot": price_snapshot,
        "calls": [
            {**call, "cost_usd": cost}
            for call, cost in zip(calls, costs, strict=True)
        ],
        "raw_secret_free_exchanges": exchanges,
        "input_tokens": sum(int(call.get("input_tokens") or 0) for call in calls),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in calls),
        "total_cost_usd": sum(known_costs) if len(known_costs) == len(calls) else None,
    }


def _model_identity(model: Any, expected_model: str) -> tuple[bool, list[str | None]]:
    records = _model_records(model, "provider_call_records")
    returned = [
        item.get("returned_model")
        for item in records
        if isinstance(item, Mapping) and item.get("status") == "success"
    ]
    if not returned:
        direct = getattr(model, "returned_model", None)
        if isinstance(direct, str) and direct:
            returned = [direct]
    exact = bool(returned) and all(value == expected_model for value in returned)
    return exact, returned


def _controller_summary(episode: Mapping[str, Any] | None) -> dict[str, Any]:
    controller = episode.get("controller") if isinstance(episode, Mapping) else None
    if not isinstance(controller, Mapping):
        return {}
    return {
        key: controller.get(key)
        for key in ("status", "model_calls", "capability_calls", "invalid_outputs")
        if key in controller
    }


def _harness_summary(episode: Mapping[str, Any] | None) -> dict[str, Any]:
    harness = episode.get("harness") if isinstance(episode, Mapping) else None
    if not isinstance(harness, Mapping):
        return {}
    return {
        key: harness.get(key)
        for key in (
            "physical_harness_verdict",
            "task_metric_passed",
            "physical_execution_passed",
            "physical_integrity_passed",
            "video_complete",
        )
        if key in harness
    }


def _classify_episode(
    episode: Any, *, model: Any, expected_model: str
) -> tuple[str, bool, bool, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(episode, Mapping):
        return (
            "infrastructure_failure",
            False,
            False,
            {},
            {},
            {"reason": "episode runner returned a non-object"},
        )
    identity_exact, returned_models = _model_identity(model, expected_model)
    model_evidence = {
        "requested_model": expected_model,
        "returned_models": returned_models,
        "exact_match": identity_exact,
    }
    controller = _controller_summary(episode)
    harness = _harness_summary(episode)
    controller_status = controller.get("status")
    if not identity_exact and controller_status == "MODEL_ERROR":
        return (
            "model_failure",
            False,
            False,
            model_evidence,
            harness,
            {"reason": "the ReCAP model call failed before an exact identity was returned"},
        )
    if not identity_exact and controller_status in {
        "INVALID_OUTPUT_BUDGET_EXHAUSTED",
        "CAPABILITY_CALL_BUDGET_EXHAUSTED",
        "WORKER_ABORTED",
    }:
        return (
            "controller_failure",
            False,
            False,
            model_evidence,
            harness,
            {"reason": f"ReCAP controller ended with {controller_status}"},
        )
    if not identity_exact:
        return (
            "evidence_incomplete",
            False,
            False,
            model_evidence,
            harness,
            {"reason": "returned model identity is absent or does not exactly match the pin"},
        )
    if harness.get("video_complete") is not True:
        return (
            "evidence_incomplete",
            False,
            False,
            model_evidence,
            harness,
            {"reason": "required continuous episode video is absent or incomplete"},
        )
    verdict = harness.get("physical_harness_verdict")
    physical_integrity = harness.get("physical_integrity_passed")
    if verdict not in {"PASS", "FAIL"} or not isinstance(physical_integrity, bool):
        return (
            "infrastructure_failure",
            False,
            False,
            model_evidence,
            harness,
            {"reason": "trusted Harness terminal fields are incomplete"},
        )
    if verdict == "PASS" and physical_integrity is not True:
        return (
            "infrastructure_failure",
            False,
            False,
            model_evidence,
            harness,
            {"reason": "Harness PASS contradicts its physical-integrity result"},
        )
    classification = "harness_pass" if verdict == "PASS" else "harness_fail"
    return classification, True, verdict == "PASS", model_evidence, harness, {}


def _exception_classification(phase: str) -> str:
    if phase == "dispatch":
        return "not_run"
    if phase == "evidence":
        return "evidence_incomplete"
    return "infrastructure_failure"


def _error_object(exc: BaseException, *, secret: str | None = None) -> dict[str, str]:
    message = str(exc)[:1000]
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return {"type": type(exc).__name__, "message": message}


def _fixed_input_record(
    *,
    manifest: ResolvedB2Manifest,
    unit: B2Unit,
    package_root: Path,
    driver_path: Path,
    capability_design_path: Path,
) -> dict[str, Any]:
    b2_source = REPOSITORY_ROOT / "autoadapter/src/autoadapter2/b2"
    return {
        "manifest_path": str(manifest.manifest_path),
        "task_suite_path": str(manifest.task_suite_path),
        "provider_manifest_path": str(manifest.provider_manifest_path),
        "provider_source_path": str(manifest.provider_source_paths[unit.model_id]),
        "reference_selection_path": str(manifest.reference_selection_path),
        "robot_package_root": str(package_root),
        "driver_path": str(driver_path),
        "capability_design_path": str(capability_design_path),
        "capability_adapter_path": str(b2_source / "capability_adapter.py"),
        "public_observation_path": str(b2_source / "public_observation.py"),
        "task_public_observation_path": str(
            b2_source / "task_public_observation.py"
        ),
        "task_harness_path": str(b2_source / "task_harness.py"),
        "recap_controller_path": str(b2_source / "recap.py"),
        "worker_protocol_path": str(b2_source / "worker_protocol.py"),
    }


def _write_secret_free_record(
    path: Path,
    value: Mapping[str, Any],
    *,
    credential: str,
) -> None:
    serialized = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
    if credential and credential in serialized:
        raise B2FormalError(f"refusing to persist credential-bearing evidence: {path}")
    _atomic_write_json(path, value)


def run_formal_unit(
    unit_id: str,
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    output_root: str | Path,
    env_file: str | Path | None = None,
    credential_loader: Callable[[Mapping[str, Any], Path | None], str] | None = None,
    model_factory: Callable[[Any, str], Any] | None = None,
    package_loader: Callable[[Path], Any] | None = None,
    episode_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run exactly one selected unit and atomically retain its terminal record."""

    manifest = resolve_manifest(manifest_path)
    unit = manifest.unit(unit_id)
    output_root_path = Path(output_root).resolve()
    terminal_path = terminal_path_for_unit(output_root_path, unit.unit_id)
    episode_output = episode_output_for_unit(output_root_path, unit.unit_id)
    if terminal_path.exists():
        raise B2FormalError(f"refusing to overwrite formal terminal: {terminal_path}")
    if episode_output.exists():
        raise B2FormalError(f"formal unit output is not fresh: {episode_output}")
    episode_output.mkdir(parents=True, exist_ok=False)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    code_version = _code_version()
    phase = "dispatch"
    terminal: dict[str, Any] | None = None
    terminal_written = False
    credential_value: str | None = None
    provider_pin: Mapping[str, Any] | None = None
    model: Any = None
    episode: Any = None
    fixed_inputs: dict[str, Any] = {}
    episode_record_path = episode_output / "episode_record.json"
    provider_record_path = episode_output / "provider_record.json"

    try:
        try:
            assert_dispatch_enabled(manifest)
        except FormalDispatchBlocked as exc:
            terminal = {
                "artifact_type": "b2_formal_unit_terminal",
                "authority": manifest.authority,
                "manifest_revision": AUTHORITY_REVISION,
                "formal_episode": True,
                "unit_id": unit.unit_id,
                **unit.as_dict(),
                "code_version": code_version,
                "utc_started_at": started_at,
                "utc_finished_at": _utc_now(),
                "wall_time_s": max(0.0, time.monotonic() - started_monotonic),
                "terminal_status": "NOT_RUN",
                "classification": "not_run",
                "evaluable": False,
                "success": False,
                "formal_dispatch_enabled": False,
                "blockers": list(manifest.blockers),
                "error": _error_object(exc),
                "model_identity": {
                    "requested_model": None,
                    "returned_models": [],
                    "exact_match": False,
                },
                "controller": {},
                "harness": {},
                "terminal_path": str(terminal_path),
            }
            _atomic_write_json(terminal_path, terminal)
            terminal_written = True
            raise

        provider_pin = manifest.provider_pins[unit.model_id]
        selected_env = Path(env_file).resolve() if env_file is not None else None
        phase = "provider"
        loader = credential_loader or _default_credential_loader
        credential = loader(provider_pin, selected_env)
        if not isinstance(credential, str) or not credential:
            raise B2FormalError("credential loader returned an empty credential")
        credential_value = credential
        provider_config = _provider_config(provider_pin, manifest.provider_manifest_path)

        phase = "model"
        model_builder = model_factory or _default_model_factory
        model = model_builder(provider_config, credential)

        package_root, driver_path, capability_design_path = manifest.reference_paths(
            unit.robot_configuration_id
        )
        if not package_root.is_dir():
            raise B2FormalError(f"canonical package is absent: {package_root}")
        if not driver_path.is_file():
            raise B2FormalError(f"selected fixed reference driver is absent: {driver_path}")
        if not capability_design_path.is_file():
            raise B2FormalError(
                f"resolved capability design is absent: {capability_design_path}"
            )
        fixed_inputs = _fixed_input_record(
            manifest=manifest,
            unit=unit,
            package_root=package_root,
            driver_path=driver_path,
            capability_design_path=capability_design_path,
        )
        phase = "package"
        package = (package_loader or _load_package)(package_root)

        phase = "episode"
        runner = episode_runner or _run_episode
        budgets = _recap_budgets()
        episode = runner(
            config=_episode_config(
                manifest=manifest,
                unit=unit,
                driver_path=driver_path,
                capability_design_path=capability_design_path,
                output_dir=episode_output,
            ),
            package=package,
            model=model,
            budgets=budgets,
        )
        phase = "evidence"
        provider_evidence = {
            "artifact_type": "b2_formal_provider_record",
            "authority": manifest.authority,
            "manifest_revision": AUTHORITY_REVISION,
            "formal_episode": True,
            "unit": unit.as_dict(),
            **_provider_evidence(
                model=model,
                provider_pin=provider_pin,
                provider_source_path=manifest.provider_source_paths[unit.model_id],
            ),
        }
        provider_manifest = _read_object(
            manifest.provider_manifest_path,
            label="B2 provider manifest",
        )
        episode_record = {
            "artifact_type": "b2_formal_episode_record",
            "authority": manifest.authority,
            "manifest_revision": AUTHORITY_REVISION,
            "formal_episode": True,
            "unit": unit.as_dict(),
            "code_version": code_version,
            "fixed_inputs": fixed_inputs,
            "fixed_controller_contract": provider_manifest.get(
                "common_controller_contract"
            ),
            "fixed_controller_budgets": asdict(budgets),
            "episode": episode,
        }
        _write_secret_free_record(
            provider_record_path,
            provider_evidence,
            credential=credential_value,
        )
        _write_secret_free_record(
            episode_record_path,
            episode_record,
            credential=credential_value,
        )
        (
            classification,
            evaluable,
            success,
            model_identity,
            harness,
            evidence_note,
        ) = _classify_episode(
            episode,
            model=model,
            expected_model=str(provider_pin["exact_model_id"]),
        )
        terminal = {
            "artifact_type": "b2_formal_unit_terminal",
            "authority": manifest.authority,
            "manifest_revision": AUTHORITY_REVISION,
            "formal_episode": True,
            "unit_id": unit.unit_id,
            **unit.as_dict(),
            "code_version": code_version,
            "utc_started_at": started_at,
            "utc_finished_at": _utc_now(),
            "wall_time_s": max(0.0, time.monotonic() - started_monotonic),
            "terminal_status": classification.upper(),
            "classification": classification,
            "evaluable": evaluable,
            "success": success,
            "formal_dispatch_enabled": True,
            "model_identity": model_identity,
            "controller": _controller_summary(episode),
            "harness": harness,
            "fixed_inputs": fixed_inputs,
            "episode_output_dir": str(episode_output),
            "episode_record_path": str(episode_record_path),
            "provider_record_path": str(provider_record_path),
            "video_requested": True,
            "error": evidence_note or None,
            "terminal_path": str(terminal_path),
        }
    except FormalDispatchBlocked:
        raise
    except Exception as exc:
        provider_record_error: dict[str, str] | None = None
        if model is not None and provider_pin is not None and not provider_record_path.exists():
            try:
                _write_secret_free_record(
                    provider_record_path,
                    {
                        "artifact_type": "b2_formal_provider_record",
                        "authority": manifest.authority,
                        "manifest_revision": AUTHORITY_REVISION,
                        "formal_episode": True,
                        "unit": unit.as_dict(),
                        **_provider_evidence(
                            model=model,
                            provider_pin=provider_pin,
                            provider_source_path=manifest.provider_source_paths[
                                unit.model_id
                            ],
                        ),
                    },
                    credential=credential_value or "",
                )
            except Exception as provider_exc:
                provider_record_error = _error_object(
                    provider_exc,
                    secret=credential_value,
                )
        classification = _exception_classification(phase)
        model_identity = {
            "requested_model": None,
            "returned_models": [],
            "exact_match": False,
        }
        if model is not None and provider_pin is not None:
            exact, returned = _model_identity(
                model,
                str(provider_pin.get("exact_model_id")),
            )
            model_identity = {
                "requested_model": provider_pin.get("exact_model_id"),
                "returned_models": returned,
                "exact_match": exact,
            }
        terminal = {
            "artifact_type": "b2_formal_unit_terminal",
            "authority": manifest.authority,
            "manifest_revision": AUTHORITY_REVISION,
            "formal_episode": True,
            "unit_id": unit.unit_id,
            **unit.as_dict(),
            "code_version": code_version,
            "utc_started_at": started_at,
            "utc_finished_at": _utc_now(),
            "wall_time_s": max(0.0, time.monotonic() - started_monotonic),
            "terminal_status": classification.upper(),
            "classification": classification,
            "evaluable": False,
            "success": False,
            "formal_dispatch_enabled": manifest.formal_dispatch_enabled,
            "model_identity": model_identity,
            "controller": _controller_summary(episode),
            "harness": _harness_summary(episode),
            "fixed_inputs": fixed_inputs,
            "episode_output_dir": str(episode_output),
            "episode_record_path": (
                str(episode_record_path) if episode_record_path.is_file() else None
            ),
            "provider_record_path": (
                str(provider_record_path) if provider_record_path.is_file() else None
            ),
            "error": _error_object(exc, secret=credential_value),
            "provider_record_error": provider_record_error,
            "terminal_path": str(terminal_path),
        }
    finally:
        if terminal is not None and not terminal_written:
            _atomic_write_json(terminal_path, terminal)

    if terminal is None:
        raise B2FormalError("B2 formal runner produced no terminal record")
    return terminal


def _recap_budgets() -> Any:
    from autoadapter2.b2.recap import RecapBudgets

    return RecapBudgets()


def _episode_config(
    *,
    manifest: ResolvedB2Manifest,
    unit: B2Unit,
    driver_path: Path,
    capability_design_path: Path,
    output_dir: Path,
) -> Any:
    from autoadapter2.b2.episode_runner import B2DiagnosticEpisodeConfig

    return B2DiagnosticEpisodeConfig(
        task_suite_path=manifest.task_suite_path,
        robot_configuration_id=unit.robot_configuration_id,
        task_id=unit.task_id,
        replicate_id=unit.replicate_id,
        driver_path=driver_path,
        capability_design_path=capability_design_path,
        output_dir=output_dir,
        record_video=True,
        wall_timeout_s=120.0,
    )


def _run_episode(**kwargs: Any) -> Mapping[str, Any]:
    from autoadapter2.b2.episode_runner import run_b2_diagnostic_episode

    return run_b2_diagnostic_episode(**kwargs)


def _load_package(path: Path) -> Any:
    from autoadapter2.libraries import load_robot_package

    return load_robot_package(path)


__all__ = [
    "AUTHORITY_DOCUMENT_ID",
    "AUTHORITY_REVISION",
    "B2FormalError",
    "B2Unit",
    "DEFAULT_MANIFEST_PATH",
    "FormalDispatchBlocked",
    "ResolvedB2Manifest",
    "assert_dispatch_enabled",
    "resolve_manifest",
    "run_formal_unit",
    "terminal_path_for_unit",
]
