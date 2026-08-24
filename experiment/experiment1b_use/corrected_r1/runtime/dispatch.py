"""Dispatch isolated, non-formal corrected Exp1b executions.

This module deliberately reuses the versioned B2 provider transport, ReCAP
budgets, identity checks, and cost accounting.  The original corrected-R1
profile remains the default; the isolated corrected-R23 profile adds only the
two fresh repeatability replicates requested by the project owner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiment.experiment1b_use.runtime import b2 as base_b2

from .episode_runner import CorrectedEpisodeConfig, run_corrected_episode
from .harness import AUDIT_ID, AUDIT_REVISION


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "config/manifest.json"
EXPECTED_MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
EXPECTED_TASK_ORDER = (
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)
EXPECTED_DISPATCH_ORDER = (*EXPECTED_TASK_ORDER, "M2_REPLACEMENTS")
R23_AUDIT_ID = "AA2-B2-CORRECTED-R23"
R23_AUDIT_REVISION = "1.0.0"
R23_TASK_ORDER = (
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)
COMPANY_MODELS = tuple(model_id for model_id in EXPECTED_MODELS if model_id != "M5")


class CorrectedDispatchError(ValueError):
    """Raised when corrected-R1 inputs or outputs are not mechanically valid."""


class CorrectedDispatchBlocked(CorrectedDispatchError):
    """Raised before credential loading when the positive-control gate is closed."""


@dataclass(frozen=True)
class CorrectedUnit:
    unit_id: str
    robot_configuration_id: str
    task_id: str
    model_id: str
    replicate_id: str
    execution_origin: str

    def as_dict(self) -> dict[str, str]:
        return {
            "unit_id": self.unit_id,
            "robot_configuration_id": self.robot_configuration_id,
            "task_id": self.task_id,
            "model_id": self.model_id,
            "replicate_id": self.replicate_id,
            "execution_origin": self.execution_origin,
        }


@dataclass(frozen=True)
class ResolvedCorrectedManifest:
    manifest_path: Path
    document: dict[str, Any]
    task_suite_path: Path
    provider_manifest_path: Path
    reference_selection_path: Path
    positive_control_index_path: Path
    provider_pins: Mapping[str, Mapping[str, Any]]
    provider_source_paths: Mapping[str, Path]
    selections: Mapping[str, Mapping[str, Any]]
    units: tuple[CorrectedUnit, ...]
    audit_document_id: str = AUDIT_ID
    audit_revision: str = AUDIT_REVISION
    evidence_prefix: str = "b2_corrected_r1"
    suite_artifact_type: str = "b2_corrected_r1_task_suite"
    positive_control_artifact_type: str = "b2_corrected_r1_positive_control_index"
    expected_task_order: tuple[str, ...] = EXPECTED_TASK_ORDER

    @property
    def audit_identity(self) -> dict[str, str]:
        return {
            "document_id": self.audit_document_id,
            "revision": self.audit_revision,
        }

    def artifact_type(self, suffix: str) -> str:
        return f"{self.evidence_prefix}_{suffix}"

    def unit(self, unit_id: str) -> CorrectedUnit:
        for unit in self.units:
            if unit.unit_id == unit_id:
                return unit
        raise CorrectedDispatchError(f"unknown corrected fresh unit: {unit_id}")

    def reference_paths(self, robot_configuration_id: str) -> tuple[Path, Path, Path]:
        try:
            selection = self.selections[robot_configuration_id]
        except KeyError as exc:
            raise CorrectedDispatchError(
                f"no corrected reference selection for {robot_configuration_id!r}"
            ) from exc
        return (
            _repository_path(selection.get("package_root"), label="package_root"),
            _repository_path(selection.get("driver_path"), label="driver_path"),
            _repository_path(
                selection.get("capability_design_path"),
                label="capability_design_path",
            ),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise CorrectedDispatchError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise CorrectedDispatchError(f"{label} must contain one JSON object")
    return value


def _required_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorrectedDispatchError(f"{label} must be a non-empty string")
    return value.strip()


def _repository_path(value: Any, *, label: str) -> Path:
    raw = _required_string(value, label=label)
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _identity(
    value: Any,
    *,
    label: str,
    document_id: str = AUDIT_ID,
    revision: str = AUDIT_REVISION,
) -> None:
    if not isinstance(value, Mapping) or dict(value) != {
        "document_id": document_id,
        "revision": revision,
    }:
        raise CorrectedDispatchError(f"{label} audit identity is invalid")


def _selection_document(path: Path) -> dict[str, Mapping[str, Any]]:
    document = _read_object(path, label="corrected reference selection")
    if (
        document.get("artifact_type") != "b2_corrected_r1_reference_selection"
        or document.get("schema_version") != "1.0"
    ):
        raise CorrectedDispatchError("corrected reference selection identity is invalid")
    _identity(document.get("audit_identity"), label="corrected reference selection")
    values = document.get("robots")
    expected_robots = ("robotstudio_so101", "unitree-go2-stock-12dof")
    if (
        not isinstance(values, list)
        or len(values) != 2
        or tuple(
            item.get("robot_configuration_id")
            for item in values
            if isinstance(item, Mapping)
        )
        != expected_robots
    ):
        raise CorrectedDispatchError("corrected reference selection robot set is invalid")
    selections: dict[str, Mapping[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping):
            raise CorrectedDispatchError("corrected reference selection entry is invalid")
        robot_id = str(item["robot_configuration_id"])
        expected_version = "1.0.2" if robot_id == "robotstudio_so101" else "1.0.1"
        if item.get("package_version") != expected_version:
            raise CorrectedDispatchError(f"{robot_id} corrected package version is invalid")
        if item.get("condition") != "skeleton-assisted":
            raise CorrectedDispatchError(f"{robot_id} corrected condition is invalid")
        expected_package = (
            REPOSITORY_ROOT
            / "autoadapter/libraries/robots"
            / robot_id
            / expected_version
        ).resolve()
        expected_paths = {
            "package_root": expected_package,
            "driver_path": (
                expected_package / "reference/fixed_capability_driver.py"
            ).resolve(),
            "capability_design_path": (
                Path(__file__).resolve().parents[1]
                / "config/reference/resolved"
                / robot_id
                / "capability_design.json"
            ).resolve(),
        }
        for field in ("package_root", "driver_path", "capability_design_path"):
            path_value = _repository_path(item.get(field), label=f"{robot_id}.{field}")
            if path_value != expected_paths[field]:
                raise CorrectedDispatchError(f"{robot_id}.{field} pin changed")
            if field == "package_root":
                if not path_value.is_dir():
                    raise CorrectedDispatchError(f"{robot_id}.{field} is absent: {path_value}")
            elif not path_value.is_file():
                raise CorrectedDispatchError(f"{robot_id}.{field} is absent: {path_value}")
        selections[robot_id] = dict(item)
    return selections


def _fresh_units(value: Any) -> tuple[CorrectedUnit, ...]:
    if not isinstance(value, list) or len(value) != 51:
        raise CorrectedDispatchError("corrected manifest must enumerate 51 fresh units")
    units: list[CorrectedUnit] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CorrectedDispatchError(f"fresh_units[{index}] must be an object")
        unit = CorrectedUnit(
            unit_id=_required_string(item.get("unit_id"), label=f"fresh_units[{index}].unit_id"),
            robot_configuration_id=_required_string(
                item.get("robot_configuration_id"),
                label=f"fresh_units[{index}].robot_configuration_id",
            ),
            task_id=_required_string(item.get("task_id"), label=f"fresh_units[{index}].task_id"),
            model_id=_required_string(item.get("model_id"), label=f"fresh_units[{index}].model_id"),
            replicate_id=_required_string(
                item.get("replicate_id"), label=f"fresh_units[{index}].replicate_id"
            ),
            execution_origin=_required_string(
                item.get("execution_origin"),
                label=f"fresh_units[{index}].execution_origin",
            ),
        )
        expected_id = (
            f"b2-corrected-r1::{unit.robot_configuration_id}::{unit.task_id}::"
            f"{unit.model_id}::R1"
        )
        if unit.unit_id != expected_id or unit.replicate_id != "R1":
            raise CorrectedDispatchError(f"fresh unit identity is invalid: {unit.unit_id}")
        if unit.model_id not in EXPECTED_MODELS:
            raise CorrectedDispatchError(f"fresh unit model is invalid: {unit.model_id}")
        if unit.execution_origin not in {"fresh_corrected", "replacement"}:
            raise CorrectedDispatchError(
                f"fresh unit execution_origin is invalid: {unit.execution_origin}"
            )
        if unit.execution_origin == "replacement":
            if not (
                unit.robot_configuration_id == "robotstudio_so101"
                and unit.task_id in {"mw_push_to_goal", "mw_sweep_into_goal"}
                and unit.model_id == "M2"
            ):
                raise CorrectedDispatchError(f"invalid replacement unit: {unit.unit_id}")
        elif unit.task_id not in EXPECTED_TASK_ORDER:
            raise CorrectedDispatchError(f"invalid corrected task: {unit.task_id}")
        elif (
            unit.task_id.startswith("GO2-")
            and unit.robot_configuration_id != "unitree-go2-stock-12dof"
        ) or (
            unit.task_id in {"mw_pick_place_wall", "mw_dial_turn"}
            and unit.robot_configuration_id != "robotstudio_so101"
        ):
            raise CorrectedDispatchError(
                f"corrected task/robot pairing is invalid: {unit.unit_id}"
            )
        units.append(unit)
    if len({unit.unit_id for unit in units}) != 51:
        raise CorrectedDispatchError("corrected manifest contains duplicate fresh units")
    counts = {task_id: sum(unit.task_id == task_id for unit in units) for task_id in EXPECTED_TASK_ORDER}
    if counts != {task_id: 7 for task_id in EXPECTED_TASK_ORDER}:
        raise CorrectedDispatchError("corrected manifest does not contain seven models per corrected task")
    if sum(unit.execution_origin == "replacement" for unit in units) != 2:
        raise CorrectedDispatchError("corrected manifest must contain two M2 replacements")
    if any(
        sum(unit.model_id == model_id for unit in units)
        != (9 if model_id == "M2" else 7)
        for model_id in EXPECTED_MODELS
    ):
        raise CorrectedDispatchError("corrected manifest fresh per-model counts are invalid")
    return tuple(units)


def _r23_units(value: Any) -> tuple[CorrectedUnit, ...]:
    if not isinstance(value, list) or len(value) != 140:
        raise CorrectedDispatchError("corrected-R23 manifest must enumerate 140 fresh units")
    units: list[CorrectedUnit] = []
    expected_pairs = {
        "mw_push_to_goal": "robotstudio_so101",
        "mw_sweep_into_goal": "robotstudio_so101",
        "mw_pick_place": "robotstudio_so101",
        "mw_pick_place_wall": "robotstudio_so101",
        "mw_dial_turn": "robotstudio_so101",
        "GO2-T02": "unitree-go2-stock-12dof",
        "GO2-T03": "unitree-go2-stock-12dof",
        "GO2-T06": "unitree-go2-stock-12dof",
        "GO2-T16": "unitree-go2-stock-12dof",
        "GO2-T17": "unitree-go2-stock-12dof",
    }
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CorrectedDispatchError(f"fresh_units[{index}] must be an object")
        unit = CorrectedUnit(
            unit_id=_required_string(item.get("unit_id"), label=f"fresh_units[{index}].unit_id"),
            robot_configuration_id=_required_string(
                item.get("robot_configuration_id"),
                label=f"fresh_units[{index}].robot_configuration_id",
            ),
            task_id=_required_string(item.get("task_id"), label=f"fresh_units[{index}].task_id"),
            model_id=_required_string(item.get("model_id"), label=f"fresh_units[{index}].model_id"),
            replicate_id=_required_string(
                item.get("replicate_id"), label=f"fresh_units[{index}].replicate_id"
            ),
            execution_origin=_required_string(
                item.get("execution_origin"),
                label=f"fresh_units[{index}].execution_origin",
            ),
        )
        expected_id = (
            f"b2-corrected-r23::{unit.robot_configuration_id}::{unit.task_id}::"
            f"{unit.model_id}::{unit.replicate_id}"
        )
        if (
            unit.unit_id != expected_id
            or unit.replicate_id not in {"R2", "R3"}
            or unit.model_id not in EXPECTED_MODELS
            or unit.execution_origin != "fresh_corrected"
            or expected_pairs.get(unit.task_id) != unit.robot_configuration_id
        ):
            raise CorrectedDispatchError(f"corrected-R23 unit identity is invalid: {unit.unit_id}")
        units.append(unit)
    if len({unit.unit_id for unit in units}) != 140:
        raise CorrectedDispatchError("corrected-R23 manifest contains duplicate units")
    expected_cells = {
        (task_id, model_id, replicate_id)
        for task_id in R23_TASK_ORDER
        for model_id in EXPECTED_MODELS
        for replicate_id in ("R2", "R3")
    }
    actual_cells = {
        (unit.task_id, unit.model_id, unit.replicate_id) for unit in units
    }
    if actual_cells != expected_cells:
        raise CorrectedDispatchError("corrected-R23 manifest is not the complete balanced block")
    return tuple(units)


def resolve_corrected_manifest(
    path: str | Path = DEFAULT_MANIFEST_PATH,
) -> ResolvedCorrectedManifest:
    """Resolve one of the two sealed corrected execution profiles."""

    manifest_path = Path(path).resolve()
    document = _read_object(manifest_path, label="corrected manifest")
    if document.get("artifact_type") == "b2_corrected_r23_manifest":
        return _resolve_r23_manifest(manifest_path, document)
    if (
        document.get("artifact_type") != "b2_corrected_r1_manifest"
        or document.get("schema_version") != "1.0"
        or document.get("formal_episode") is not False
        or document.get("formal_denominator_entry") is not False
    ):
        raise CorrectedDispatchError("corrected-R1 manifest identity/claim boundary is invalid")
    _identity(document.get("audit_identity"), label="corrected-R1 manifest")
    if (
        document.get("models") != list(EXPECTED_MODELS)
        or document.get("replicate_id") != "R1"
        or document.get("corrected_r1_units") != 70
        or document.get("fresh_execution_units") != 51
        or document.get("retained_rejudication_units") != 19
        or document.get("old_formal_plan_unchanged") != 210
        or tuple(document.get("dispatch_order", ())) != EXPECTED_DISPATCH_ORDER
        or document.get("retry_policy")
        != {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        }
    ):
        raise CorrectedDispatchError("corrected-R1 cohort declaration is invalid")
    retained = document.get("retained_units")
    if not isinstance(retained, list) or len(retained) != 19:
        raise CorrectedDispatchError("corrected-R1 retained declaration must contain 19 units")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise CorrectedDispatchError("corrected-R1 manifest paths are absent")
    task_suite_path = _repository_path(paths.get("task_suite"), label="paths.task_suite")
    provider_manifest_path = _repository_path(
        paths.get("provider_manifest"), label="paths.provider_manifest"
    )
    reference_selection_path = _repository_path(
        paths.get("reference_selection"), label="paths.reference_selection"
    )
    positive_control_index_path = _repository_path(
        paths.get("positive_control_index"), label="paths.positive_control_index"
    )
    for label, file_path in (
        ("corrected task suite", task_suite_path),
        ("provider manifest", provider_manifest_path),
        ("reference selection", reference_selection_path),
    ):
        if not file_path.is_file():
            raise CorrectedDispatchError(f"{label} is absent: {file_path}")
    suite = _read_object(task_suite_path, label="corrected task suite")
    if (
        suite.get("artifact_type") != "b2_corrected_r1_task_suite"
        or suite.get("schema_version") != "1.0"
        or suite.get("formal_episode") is not False
        or suite.get("task_count") != 10
        or suite.get("replicate_plan") != {"replicate_ids": ["R1"]}
    ):
        raise CorrectedDispatchError("corrected task suite identity is invalid")
    _identity(suite.get("audit_identity"), label="corrected task suite")
    selections = _selection_document(reference_selection_path)
    embedded = document.get("reference_selection")
    if not isinstance(embedded, Mapping) or embedded.get("robots") != [
        dict(selections[robot_id])
        for robot_id in ("robotstudio_so101", "unitree-go2-stock-12dof")
    ]:
        raise CorrectedDispatchError("embedded corrected reference selection changed")
    try:
        pins, source_paths, provider_ready = base_b2._validate_provider_manifest(
            provider_manifest_path,
            models=EXPECTED_MODELS,
        )
    except base_b2.B2FormalError as exc:
        raise CorrectedDispatchError(str(exc)) from exc
    if not provider_ready:
        raise CorrectedDispatchError("source B2 provider pins are not dispatch-ready")
    return ResolvedCorrectedManifest(
        manifest_path=manifest_path,
        document=document,
        task_suite_path=task_suite_path,
        provider_manifest_path=provider_manifest_path,
        reference_selection_path=reference_selection_path,
        positive_control_index_path=positive_control_index_path,
        provider_pins=pins,
        provider_source_paths=source_paths,
        selections=selections,
        units=_fresh_units(document.get("fresh_units")),
    )


def _resolve_r23_manifest(
    manifest_path: Path, document: dict[str, Any]
) -> ResolvedCorrectedManifest:
    if (
        document.get("schema_version") != "1.0"
        or document.get("formal_episode") is not False
        or document.get("formal_denominator_entry") is not False
    ):
        raise CorrectedDispatchError("corrected-R23 manifest claim boundary is invalid")
    _identity(
        document.get("audit_identity"),
        label="corrected-R23 manifest",
        document_id=R23_AUDIT_ID,
        revision=R23_AUDIT_REVISION,
    )
    if (
        document.get("models") != list(EXPECTED_MODELS)
        or document.get("replicate_ids") != ["R2", "R3"]
        or document.get("fresh_execution_units") != 140
        or document.get("retained_rejudication_units") != 0
        or document.get("replacement_units") != 0
        or document.get("corrected_combined_plan") != 210
        or document.get("source_formal_plan_modified") is not False
        or tuple(document.get("dispatch_order", ())) != R23_TASK_ORDER
        or document.get("retry_policy")
        != {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        }
    ):
        raise CorrectedDispatchError("corrected-R23 cohort declaration is invalid")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise CorrectedDispatchError("corrected-R23 manifest paths are absent")
    task_suite_path = _repository_path(paths.get("task_suite"), label="paths.task_suite")
    provider_manifest_path = _repository_path(
        paths.get("provider_manifest"), label="paths.provider_manifest"
    )
    reference_selection_path = _repository_path(
        paths.get("reference_selection"), label="paths.reference_selection"
    )
    positive_control_index_path = _repository_path(
        paths.get("positive_control_index"), label="paths.positive_control_index"
    )
    for label, file_path in (
        ("corrected-R23 task suite", task_suite_path),
        ("provider manifest", provider_manifest_path),
        ("reference selection", reference_selection_path),
    ):
        if not file_path.is_file():
            raise CorrectedDispatchError(f"{label} is absent: {file_path}")
    suite = _read_object(task_suite_path, label="corrected-R23 task suite")
    if (
        suite.get("artifact_type") != "b2_corrected_r23_task_suite"
        or suite.get("schema_version") != "1.0"
        or suite.get("formal_episode") is not False
        or suite.get("task_count") != 10
        or suite.get("replicate_plan")
        != {"replicate_ids": ["R2", "R3"]}
        or suite.get("source_corrected_r1", {}).get(
            "inputs_changed_beyond_replicate_identity"
        )
        is not False
    ):
        raise CorrectedDispatchError("corrected-R23 task suite identity is invalid")
    _identity(
        suite.get("audit_identity"),
        label="corrected-R23 task suite",
        document_id=R23_AUDIT_ID,
        revision=R23_AUDIT_REVISION,
    )
    selections = _selection_document(reference_selection_path)
    embedded = document.get("reference_selection")
    if not isinstance(embedded, Mapping) or embedded.get("robots") != [
        dict(selections[robot_id])
        for robot_id in ("robotstudio_so101", "unitree-go2-stock-12dof")
    ]:
        raise CorrectedDispatchError("embedded corrected reference selection changed")
    try:
        pins, source_paths, provider_ready = base_b2._validate_provider_manifest(
            provider_manifest_path,
            models=EXPECTED_MODELS,
        )
    except base_b2.B2FormalError as exc:
        raise CorrectedDispatchError(str(exc)) from exc
    if not provider_ready:
        raise CorrectedDispatchError("source B2 provider pins are not dispatch-ready")
    return ResolvedCorrectedManifest(
        manifest_path=manifest_path,
        document=document,
        task_suite_path=task_suite_path,
        provider_manifest_path=provider_manifest_path,
        reference_selection_path=reference_selection_path,
        positive_control_index_path=positive_control_index_path,
        provider_pins=pins,
        provider_source_paths=source_paths,
        selections=selections,
        units=_r23_units(document.get("fresh_units")),
        audit_document_id=R23_AUDIT_ID,
        audit_revision=R23_AUDIT_REVISION,
        evidence_prefix="b2_corrected_r23",
        suite_artifact_type="b2_corrected_r23_task_suite",
        positive_control_artifact_type="b2_corrected_r23_positive_control_index",
        expected_task_order=R23_TASK_ORDER,
    )


def assert_positive_control_gate(manifest: ResolvedCorrectedManifest) -> dict[str, Any]:
    """Require the profile's fixed-driver/Harness/video controls before credentials."""

    path = manifest.positive_control_index_path
    try:
        document = _read_object(path, label="corrected positive-control index")
    except CorrectedDispatchError as exc:
        raise CorrectedDispatchBlocked(str(exc)) from exc
    try:
        if (
            document.get("artifact_type")
            != manifest.positive_control_artifact_type
            or document.get("schema_version") != "1.0"
        ):
            raise CorrectedDispatchError("positive-control index identity is invalid")
        _identity(
            document.get("audit_identity"),
            label="positive-control index",
            document_id=manifest.audit_document_id,
            revision=manifest.audit_revision,
        )
        if manifest.evidence_prefix == "b2_corrected_r23" and (
            document.get("control_replicate_id") != "R2"
            or document.get("covered_replicates") != ["R2", "R3"]
        ):
            raise CorrectedDispatchError(
                "corrected-R23 positive-control replicate coverage is invalid"
            )
        if tuple(document.get("required_task_ids", ())) != manifest.expected_task_order:
            raise CorrectedDispatchError("positive-control required_task_ids changed")
        results = document.get("results")
        if not isinstance(results, list) or len(results) != len(
            manifest.expected_task_order
        ):
            raise CorrectedDispatchError(
                "positive-control index has the wrong number of results"
            )
        task_ids: list[str] = []
        for index, item in enumerate(results):
            if not isinstance(item, Mapping):
                raise CorrectedDispatchError(f"positive-control result {index} is invalid")
            task_id = _required_string(
                item.get("task_id"), label=f"positive-control result {index}.task_id"
            )
            task_ids.append(task_id)
            if (
                (
                    manifest.evidence_prefix == "b2_corrected_r23"
                    and item.get("replicate_id") != "R2"
                )
                or
                item.get("status") != "PASS"
                or item.get("passed") is not True
                or item.get("trusted_harness") is not True
                or item.get("video_complete") is not True
            ):
                raise CorrectedDispatchError(
                    f"positive-control gate failed for {task_id}"
                )
            record_path = _repository_path(
                item.get("record_path"), label=f"{task_id}.record_path"
            )
            video_path = _repository_path(
                item.get("video_path"), label=f"{task_id}.video_path"
            )
            if not record_path.is_file() or not video_path.is_file():
                raise CorrectedDispatchError(
                    f"positive-control evidence is absent for {task_id}"
                )
        if tuple(task_ids) != manifest.expected_task_order or len(set(task_ids)) != len(
            manifest.expected_task_order
        ):
            raise CorrectedDispatchError("positive-control results changed order/set")
    except CorrectedDispatchError as exc:
        raise CorrectedDispatchBlocked(str(exc)) from exc
    return document


def corrected_terminal_path(output_root: str | Path, unit_id: str) -> Path:
    safe = unit_id.replace("::", "__")
    return Path(output_root).resolve() / "terminals" / f"{safe}.json"


def corrected_episode_output(output_root: str | Path, unit_id: str) -> Path:
    safe = unit_id.replace("::", "__")
    return Path(output_root).resolve() / "episodes" / safe


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _code_version() -> dict[str, Any]:
    value = base_b2._code_version()
    return {
        **value,
        "formal_runtime_path": None,
        "corrected_runtime_path": str(Path(__file__).resolve()),
        "corrected_episode_runner_path": str(
            Path(__file__).resolve().with_name("episode_runner.py")
        ),
    }


def _fixed_input_record(
    *,
    manifest: ResolvedCorrectedManifest,
    unit: CorrectedUnit,
    package_root: Path,
    driver_path: Path,
    capability_design_path: Path,
) -> dict[str, Any]:
    return {
        "manifest_path": str(manifest.manifest_path),
        "task_suite_path": str(manifest.task_suite_path),
        "provider_manifest_path": str(manifest.provider_manifest_path),
        "provider_source_path": str(manifest.provider_source_paths[unit.model_id]),
        "reference_selection_path": str(manifest.reference_selection_path),
        "positive_control_index_path": str(manifest.positive_control_index_path),
        "robot_package_root": str(package_root),
        "driver_path": str(driver_path),
        "capability_design_path": str(capability_design_path),
        "corrected_harness_path": str(Path(__file__).resolve().with_name("harness.py")),
        "corrected_episode_runner_path": str(
            Path(__file__).resolve().with_name("episode_runner.py")
        ),
        "original_incomplete_terminal_path": str(_replacement_source_path(unit))
        if unit.execution_origin == "replacement"
        else None,
    }


def _replacement_source_path(unit: CorrectedUnit) -> Path:
    if unit.execution_origin != "replacement":
        raise CorrectedDispatchError("only replacement units have an original terminal")
    run_name = {
        "mw_push_to_goal": "formal-b2-v014-batch001-company",
        "mw_sweep_into_goal": "formal-b2-v014-batch002-sweep-company",
    }.get(unit.task_id)
    if run_name is None or unit.model_id != "M2":
        raise CorrectedDispatchError(f"invalid replacement source: {unit.unit_id}")
    old_unit_id = (
        f"b2::robotstudio_so101::{unit.task_id}::{unit.model_id}::R1"
    )
    return (
        REPOSITORY_ROOT
        / "experiment/experiment1b_use/runs"
        / run_name
        / "terminals"
        / f"{old_unit_id.replace('::', '__')}.json"
    ).resolve()


def _write_secret_free_record(
    path: Path, value: Mapping[str, Any], *, credential: str
) -> None:
    serialized = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
    if credential and credential in serialized:
        raise CorrectedDispatchError(
            f"refusing to persist credential-bearing evidence: {path}"
        )
    _atomic_write_json(path, value)


def _episode_video_path(episode: Any) -> str | None:
    if not isinstance(episode, Mapping):
        return None
    metadata = episode.get("episode")
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("video_path")
    return value if isinstance(value, str) and value else None


def _load_package(path: Path) -> Any:
    from autoadapter2.libraries import load_robot_package

    return load_robot_package(path)


def run_corrected_unit(
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
    """Run one fresh corrected unit and always retain a secret-free terminal."""

    manifest = resolve_corrected_manifest(manifest_path)
    unit = manifest.unit(unit_id)
    output_root_path = Path(output_root).resolve()
    terminal_path = corrected_terminal_path(output_root_path, unit.unit_id)
    episode_output = corrected_episode_output(output_root_path, unit.unit_id)
    if terminal_path.exists():
        raise CorrectedDispatchError(f"refusing to overwrite corrected terminal: {terminal_path}")
    if episode_output.exists():
        raise CorrectedDispatchError(f"corrected unit output is not fresh: {episode_output}")

    # This gate intentionally precedes credential loading, model construction,
    # and creation of the unit's episode directory.
    assert_positive_control_gate(manifest)
    if env_file is None and credential_loader is None:
        raise CorrectedDispatchError("an explicit credential env file is required")

    episode_output.mkdir(parents=True, exist_ok=False)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    code_version = _code_version()
    phase = "provider"
    credential_value: str | None = None
    provider_pin: Mapping[str, Any] | None = None
    model: Any = None
    episode: Any = None
    fixed_inputs: dict[str, Any] = {}
    terminal: dict[str, Any] | None = None
    episode_record_path = episode_output / "episode_record.json"
    provider_record_path = episode_output / "provider_record.json"
    try:
        provider_pin = manifest.provider_pins[unit.model_id]
        selected_env = Path(env_file).resolve() if env_file is not None else None
        credential = (credential_loader or base_b2._default_credential_loader)(
            provider_pin, selected_env
        )
        if not isinstance(credential, str) or not credential:
            raise CorrectedDispatchError("credential loader returned an empty credential")
        credential_value = credential
        provider_config = base_b2._provider_config(
            provider_pin, manifest.provider_manifest_path
        )
        phase = "model"
        model = (model_factory or base_b2._default_model_factory)(
            provider_config, credential
        )
        package_root, driver_path, capability_design_path = manifest.reference_paths(
            unit.robot_configuration_id
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
        budgets = base_b2._recap_budgets()
        episode = (episode_runner or run_corrected_episode)(
            config=CorrectedEpisodeConfig(
                task_suite_path=manifest.task_suite_path,
                robot_configuration_id=unit.robot_configuration_id,
                task_id=unit.task_id,
                replicate_id=unit.replicate_id,
                driver_path=driver_path,
                capability_design_path=capability_design_path,
                output_dir=episode_output,
                record_video=True,
                wall_timeout_s=120.0,
            ),
            package=package,
            model=model,
            budgets=budgets,
        )
        phase = "evidence"
        provider_evidence = {
            "artifact_type": manifest.artifact_type("provider_record"),
            "audit_identity": manifest.audit_identity,
            "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
            "formal_episode": False,
            "execution_origin": unit.execution_origin,
            "unit": unit.as_dict(),
            **base_b2._provider_evidence(
                model=model,
                provider_pin=provider_pin,
                provider_source_path=manifest.provider_source_paths[unit.model_id],
            ),
        }
        provider_manifest = _read_object(
            manifest.provider_manifest_path, label="source B2 provider manifest"
        )
        episode_record = {
            "artifact_type": manifest.artifact_type("episode_record"),
            "audit_identity": manifest.audit_identity,
            "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
            "formal_episode": False,
            "execution_origin": unit.execution_origin,
            "unit": unit.as_dict(),
            "code_version": code_version,
            "fixed_inputs": fixed_inputs,
            "fixed_controller_contract": provider_manifest.get("common_controller_contract"),
            "fixed_controller_budgets": asdict(budgets),
            "episode": episode,
        }
        _write_secret_free_record(
            provider_record_path, provider_evidence, credential=credential_value
        )
        _write_secret_free_record(
            episode_record_path, episode_record, credential=credential_value
        )
        (
            classification,
            evaluable,
            success,
            model_identity,
            harness,
            evidence_note,
        ) = base_b2._classify_episode(
            episode,
            model=model,
            expected_model=str(provider_pin["exact_model_id"]),
        )
        terminal = {
            "artifact_type": manifest.artifact_type("unit_terminal"),
            "audit_identity": manifest.audit_identity,
            "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
            "formal_episode": False,
            "formal_denominator_entry": False,
            **unit.as_dict(),
            "original_incomplete_terminal_path": str(_replacement_source_path(unit))
            if unit.execution_origin == "replacement"
            else None,
            "code_version": code_version,
            "utc_started_at": started_at,
            "utc_finished_at": _utc_now(),
            "wall_time_s": max(0.0, time.monotonic() - started_monotonic),
            "terminal_status": classification.upper(),
            "classification": classification,
            "evaluable": evaluable,
            "success": success,
            "model_identity": model_identity,
            "controller": base_b2._controller_summary(episode),
            "harness": harness,
            "fixed_inputs": fixed_inputs,
            "episode_output_dir": str(episode_output),
            "episode_record_path": str(episode_record_path),
            "provider_record_path": str(provider_record_path),
            "provider_calls": len(provider_evidence["calls"]),
            "input_tokens": provider_evidence["input_tokens"],
            "output_tokens": provider_evidence["output_tokens"],
            "total_cost_usd": provider_evidence["total_cost_usd"],
            "video_requested": True,
            "video_path": _episode_video_path(episode),
            "error": evidence_note or None,
            "terminal_path": str(terminal_path),
        }
    except Exception as exc:
        provider_record_error: dict[str, str] | None = None
        provider_evidence: dict[str, Any] | None = None
        if model is not None and provider_pin is not None:
            provider_evidence = base_b2._provider_evidence(
                model=model,
                provider_pin=provider_pin,
                provider_source_path=manifest.provider_source_paths[unit.model_id],
            )
            if not provider_record_path.exists():
                try:
                    _write_secret_free_record(
                        provider_record_path,
                        {
                            "artifact_type": manifest.artifact_type("provider_record"),
                            "audit_identity": manifest.audit_identity,
                            "formal_episode": False,
                            "execution_origin": unit.execution_origin,
                            "unit": unit.as_dict(),
                            **provider_evidence,
                        },
                        credential=credential_value or "",
                    )
                except Exception as provider_exc:
                    provider_record_error = base_b2._error_object(
                        provider_exc, secret=credential_value
                    )
        classification = base_b2._exception_classification(phase)
        requested_model = provider_pin.get("exact_model_id") if provider_pin else None
        exact, returned = (
            base_b2._model_identity(model, str(requested_model))
            if model is not None and requested_model is not None
            else (False, [])
        )
        terminal = {
            "artifact_type": manifest.artifact_type("unit_terminal"),
            "audit_identity": manifest.audit_identity,
            "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
            "formal_episode": False,
            "formal_denominator_entry": False,
            **unit.as_dict(),
            "original_incomplete_terminal_path": str(_replacement_source_path(unit))
            if unit.execution_origin == "replacement"
            else None,
            "code_version": code_version,
            "utc_started_at": started_at,
            "utc_finished_at": _utc_now(),
            "wall_time_s": max(0.0, time.monotonic() - started_monotonic),
            "terminal_status": classification.upper(),
            "classification": classification,
            "evaluable": False,
            "success": False,
            "model_identity": {
                "requested_model": requested_model,
                "returned_models": returned,
                "exact_match": exact,
            },
            "controller": base_b2._controller_summary(episode),
            "harness": base_b2._harness_summary(episode),
            "fixed_inputs": fixed_inputs,
            "episode_output_dir": str(episode_output),
            "episode_record_path": str(episode_record_path) if episode_record_path.is_file() else None,
            "provider_record_path": str(provider_record_path) if provider_record_path.is_file() else None,
            "provider_calls": len(provider_evidence["calls"]) if provider_evidence else 0,
            "input_tokens": provider_evidence["input_tokens"] if provider_evidence else 0,
            "output_tokens": provider_evidence["output_tokens"] if provider_evidence else 0,
            "total_cost_usd": provider_evidence["total_cost_usd"] if provider_evidence else None,
            "video_requested": True,
            "video_path": _episode_video_path(episode),
            "error": base_b2._error_object(exc, secret=credential_value),
            "provider_record_error": provider_record_error,
            "terminal_path": str(terminal_path),
        }
    finally:
        if terminal is not None:
            _atomic_write_json(terminal_path, terminal)
    if terminal is None:
        raise CorrectedDispatchError("corrected runner produced no terminal record")
    return terminal


def select_units(
    manifest: ResolvedCorrectedManifest,
    *,
    task_ids: Sequence[str] | None = None,
    unit_ids: Sequence[str] | None = None,
    replicate_ids: Sequence[str] | None = None,
) -> list[CorrectedUnit]:
    if task_ids is not None and unit_ids is not None:
        raise CorrectedDispatchError("select task_ids or unit_ids, not both")
    if unit_ids is not None:
        if not unit_ids or len(unit_ids) != len(set(unit_ids)):
            raise CorrectedDispatchError("unit selection is empty or contains duplicates")
        selected = [manifest.unit(unit_id) for unit_id in unit_ids]
    elif task_ids is None:
        selected = list(manifest.units)
    else:
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise CorrectedDispatchError("task selection is empty or contains duplicates")
        expanded: list[str] = []
        for task_id in task_ids:
            if task_id == "M2_REPLACEMENTS" and manifest.evidence_prefix == "b2_corrected_r1":
                expanded.extend(("mw_push_to_goal", "mw_sweep_into_goal"))
            elif task_id in manifest.expected_task_order or (
                manifest.evidence_prefix == "b2_corrected_r1"
                and task_id in {"mw_push_to_goal", "mw_sweep_into_goal"}
            ):
                expanded.append(task_id)
            else:
                raise CorrectedDispatchError(f"unknown corrected task filter: {task_id}")
        selected = [unit for unit in manifest.units if unit.task_id in expanded]
    if replicate_ids is not None:
        if not replicate_ids or len(replicate_ids) != len(set(replicate_ids)):
            raise CorrectedDispatchError(
                "replicate selection is empty or contains duplicates"
            )
        available = {unit.replicate_id for unit in manifest.units}
        if any(replicate_id not in available for replicate_id in replicate_ids):
            raise CorrectedDispatchError("replicate selection is outside the manifest")
        selected = [
            unit for unit in selected if unit.replicate_id in set(replicate_ids)
        ]
    if not selected:
        raise CorrectedDispatchError("corrected scheduler selection is empty")
    return selected


def _scheduler_failure_terminal(
    *,
    unit: CorrectedUnit,
    manifest: ResolvedCorrectedManifest,
    expected_model: str,
    terminal_path: Path,
    reason: str,
    process_returncode: int | None,
) -> dict[str, Any]:
    terminal = {
        "artifact_type": manifest.artifact_type("unit_terminal"),
        "audit_identity": manifest.audit_identity,
        "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "formal_episode": False,
        "formal_denominator_entry": False,
        **unit.as_dict(),
        "original_incomplete_terminal_path": str(_replacement_source_path(unit))
        if unit.execution_origin == "replacement"
        else None,
        "code_version": _code_version(),
        "utc_started_at": None,
        "utc_finished_at": _utc_now(),
        "terminal_status": "INFRASTRUCTURE_FAILURE",
        "classification": "infrastructure_failure",
        "evaluable": False,
        "success": False,
        "model_identity": {
            "requested_model": expected_model,
            "returned_models": [],
            "exact_match": False,
        },
        "controller": {},
        "harness": {},
        "provider_record_path": None,
        "provider_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost_usd": None,
        "video_requested": True,
        "video_path": None,
        "error": {
            "type": "SchedulerSubprocessFailure",
            "message": reason,
            "process_returncode": process_returncode,
        },
        "terminal_path": str(terminal_path),
    }
    _atomic_write_json(terminal_path, terminal)
    return terminal


def _invoke_attempt(
    *,
    unit: CorrectedUnit,
    manifest: ResolvedCorrectedManifest,
    attempt_root: Path,
    env_file: Path,
    process_runner: Callable[..., Any],
) -> dict[str, Any]:
    terminal_path = corrected_terminal_path(attempt_root, unit.unit_id)
    command = [
        sys.executable,
        str(Path(__file__).resolve().with_name("run_corrected.py")),
        "--manifest",
        str(manifest.manifest_path),
        "--unit-id",
        unit.unit_id,
        "--output",
        str(attempt_root),
        "--env-file",
        str(env_file),
    ]
    try:
        completed = process_runner(
            command,
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        terminal = _scheduler_failure_terminal(
            unit=unit,
            manifest=manifest,
            expected_model=str(manifest.provider_pins[unit.model_id]["exact_model_id"]),
            terminal_path=terminal_path,
            reason=f"scheduler could not spawn unit process: {type(exc).__name__}",
            process_returncode=None,
        )
        return {
            "terminal": terminal,
            "terminal_path": str(terminal_path),
            "process_returncode": None,
        }
    try:
        terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        terminal = _scheduler_failure_terminal(
            unit=unit,
            manifest=manifest,
            expected_model=str(manifest.provider_pins[unit.model_id]["exact_model_id"]),
            terminal_path=terminal_path,
            reason=(
                "unit process exited without a readable terminal record "
                f"(returncode={completed.returncode})"
            ),
            process_returncode=completed.returncode,
        )
    allowed_classifications = {
        "harness_pass",
        "harness_fail",
        "controller_failure",
        "model_failure",
        "evidence_incomplete",
        "infrastructure_failure",
    }
    if not (
        isinstance(terminal, dict)
        and terminal.get("artifact_type") == manifest.artifact_type("unit_terminal")
        and terminal.get("audit_identity") == manifest.audit_identity
        and terminal.get("formal_episode") is False
        and terminal.get("formal_denominator_entry") is False
        and terminal.get("unit_id") == unit.unit_id
        and terminal.get("execution_origin") == unit.execution_origin
        and terminal.get("classification") in allowed_classifications
    ):
        terminal = _scheduler_failure_terminal(
            unit=unit,
            manifest=manifest,
            expected_model=str(manifest.provider_pins[unit.model_id]["exact_model_id"]),
            terminal_path=terminal_path,
            reason="unit process returned an incompatible corrected terminal",
            process_returncode=completed.returncode,
        )
    return {
        "terminal": terminal,
        "terminal_path": str(terminal_path),
        "process_returncode": completed.returncode,
    }


def _run_with_retry(
    *,
    unit: CorrectedUnit,
    manifest: ResolvedCorrectedManifest,
    output_root: Path,
    env_file: Path,
    process_runner: Callable[..., Any],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt_number in (1, 2):
        attempt_root = output_root / "attempts" / f"attempt-{attempt_number}"
        result = _invoke_attempt(
            unit=unit,
            manifest=manifest,
            attempt_root=attempt_root,
            env_file=env_file,
            process_runner=process_runner,
        )
        terminal = result.pop("terminal")
        attempts.append(
            {
                "attempt_number": attempt_number,
                **result,
                "classification": terminal.get("classification"),
                "evaluable": terminal.get("evaluable"),
                "success": terminal.get("success"),
            }
        )
        if terminal.get("classification") != "infrastructure_failure":
            break
    selected = attempts[-1]
    return {
        "unit_id": unit.unit_id,
        "execution_origin": unit.execution_origin,
        "provider_lane": "m5" if unit.model_id == "M5" else "company",
        "attempts": attempts,
        "retry_used": len(attempts) == 2,
        "terminal_path": selected["terminal_path"],
        "classification": selected["classification"],
        "evaluable": selected["evaluable"],
        "success": selected["success"],
    }


def run_corrected_scheduler(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    output_root: str | Path,
    company_env_file: str | Path,
    m5_env_file: str | Path,
    task_ids: Sequence[str] | None = None,
    unit_ids: Sequence[str] | None = None,
    replicate_ids: Sequence[str] | None = None,
    company_workers: int = 3,
    m5_workers: int = 1,
    process_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Run selected units with a three-worker company lane and one M5 lane."""

    if company_workers != 3 or m5_workers != 1:
        raise CorrectedDispatchError(
            "corrected scheduler is fixed to company_workers=3 and m5_workers=1"
        )
    manifest = resolve_corrected_manifest(manifest_path)
    assert_positive_control_gate(manifest)
    selected = select_units(
        manifest,
        task_ids=task_ids,
        unit_ids=unit_ids,
        replicate_ids=replicate_ids,
    )
    output_path = Path(output_root).resolve()
    if output_path.exists():
        raise CorrectedDispatchError(
            f"corrected scheduler requires a fresh output directory: {output_path}"
        )
    company_env = Path(company_env_file).resolve()
    m5_env = Path(m5_env_file).resolve()
    if not company_env.is_file() or not m5_env.is_file():
        raise CorrectedDispatchError("both explicit scheduler env files must exist")
    output_path.mkdir(parents=True, exist_ok=False)
    started_at = _utc_now()
    records: list[dict[str, Any]] = []
    futures: dict[Future[dict[str, Any]], CorrectedUnit] = {}
    company_units = [unit for unit in selected if unit.model_id != "M5"]
    m5_units = [unit for unit in selected if unit.model_id == "M5"]
    with (
        ThreadPoolExecutor(max_workers=company_workers) as company_pool,
        ThreadPoolExecutor(max_workers=m5_workers) as m5_pool,
    ):
        for unit in company_units:
            futures[
                company_pool.submit(
                    _run_with_retry,
                    unit=unit,
                    manifest=manifest,
                    output_root=output_path,
                    env_file=company_env,
                    process_runner=process_runner,
                )
            ] = unit
        for unit in m5_units:
            futures[
                m5_pool.submit(
                    _run_with_retry,
                    unit=unit,
                    manifest=manifest,
                    output_root=output_path,
                    env_file=m5_env,
                    process_runner=process_runner,
                )
            ] = unit
        for future in as_completed(futures):
            unit = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:
                records.append(
                    {
                        "unit_id": unit.unit_id,
                        "execution_origin": unit.execution_origin,
                        "provider_lane": "m5" if unit.model_id == "M5" else "company",
                        "attempts": [],
                        "retry_used": False,
                        "terminal_path": None,
                        "classification": "infrastructure_absence",
                        "evaluable": False,
                        "success": False,
                        "error": base_b2._error_object(exc),
                    }
                )
    selection_order = {unit.unit_id: index for index, unit in enumerate(selected)}
    records.sort(key=lambda record: selection_order[record["unit_id"]])
    scheduler = {
        "artifact_type": manifest.artifact_type("scheduler"),
        "schema_version": "1.0",
        "audit_identity": manifest.audit_identity,
        "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "formal_episode": False,
        "formal_denominator_entry": False,
        "manifest_path": str(manifest.manifest_path),
        "positive_control_index_path": str(manifest.positive_control_index_path),
        "planned_unit_ids": [unit.unit_id for unit in selected],
        "task_filters": list(task_ids) if task_ids is not None else None,
        "replicate_filters": list(replicate_ids)
        if replicate_ids is not None
        else None,
        "company_workers": company_workers,
        "m5_workers": m5_workers,
        "total_worker_limit": company_workers + m5_workers,
        "retry_policy": {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        },
        "records": records,
        "utc_started_at": started_at,
        "utc_finished_at": _utc_now(),
    }
    scheduler_path = output_path / "scheduler.json"
    _atomic_write_json(scheduler_path, scheduler)
    scheduler["scheduler_path"] = str(scheduler_path)
    return scheduler


__all__ = [
    "COMPANY_MODELS",
    "CorrectedDispatchBlocked",
    "CorrectedDispatchError",
    "CorrectedUnit",
    "DEFAULT_MANIFEST_PATH",
    "EXPECTED_TASK_ORDER",
    "ResolvedCorrectedManifest",
    "assert_positive_control_gate",
    "corrected_episode_output",
    "corrected_terminal_path",
    "resolve_corrected_manifest",
    "run_corrected_scheduler",
    "run_corrected_unit",
    "select_units",
]
