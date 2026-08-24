#!/usr/bin/env python3
"""Build the isolated, non-formal Exp1b corrected-R1 inputs."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiment.experiment1b_use.config.task_suite import build_suite as base_suite  # noqa: E402


AUDIT_ID = "AA2-B2-CORRECTED-R1"
AUDIT_REVISION = "1.0.0"
MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
SELECTIONS = (
    (
        "robotstudio_so101",
        "1.0.2",
        (
            "mw_push_to_goal",
            "mw_sweep_into_goal",
            "mw_pick_place",
            "mw_pick_place_wall",
            "mw_dial_turn",
        ),
    ),
    (
        "unitree-go2-stock-12dof",
        "1.0.1",
        ("GO2-T02", "GO2-T03", "GO2-T06", "GO2-T16", "GO2-T17"),
    ),
)

OLD_PROVIDER_MANIFEST = (
    REPOSITORY_ROOT / "experiment/experiment1b_use/config/providers/manifest.json"
)
OLD_DESIGNS = {
    robot_id: REPOSITORY_ROOT
    / "experiment/experiment1b_use/validation/reference/resolved"
    / robot_id
    / "capability_design.json"
    for robot_id, _version, _tasks in SELECTIONS
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPOSITORY_ROOT))


def _package_root(robot_id: str, version: str) -> Path:
    return (
        REPOSITORY_ROOT
        / "autoadapter/libraries/robots"
        / robot_id
        / version
    )


def _task_suite() -> dict[str, Any]:
    robot_suites: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    for robot_id, version, selected_task_ids in SELECTIONS:
        package_root = _package_root(robot_id, version)
        tasks_root = package_root / "tasks"
        catalog_path = tasks_root / "catalog.json"
        instances_path = tasks_root / "private/instances.json"
        bindings_path = tasks_root / "private/bindings.json"
        guards_path = tasks_root / "private/guards.json"
        sources_path = tasks_root / "sources.json"

        catalog = base_suite._read_json(catalog_path)
        instances_document = base_suite._read_json(instances_path)
        bindings_document = base_suite._read_json(bindings_path)
        guards_document = base_suite._read_json(guards_path)
        sources_document = base_suite._read_json(sources_path)
        snapshot_id = catalog.get("snapshot_id")
        if (
            catalog.get("robot_configuration_id") != robot_id
            or catalog.get("package_version") != version
            or not isinstance(snapshot_id, str)
        ):
            raise ValueError(f"corrected package identity mismatch for {robot_id}")
        for document, path in (
            (instances_document, instances_path),
            (bindings_document, bindings_path),
            (guards_document, guards_path),
        ):
            if (
                document.get("robot_configuration_id") != robot_id
                or document.get("package_version") != version
                or document.get("task_snapshot_id") != snapshot_id
            ):
                raise ValueError(f"private package identity mismatch in {path}")

        tasks = base_suite._index(
            base_suite._items(catalog, "tasks", path=catalog_path),
            "task_id",
            label="catalog",
        )
        instances_by_task: dict[str, list[dict[str, Any]]] = {}
        for instance in base_suite._items(
            instances_document, "instances", path=instances_path
        ):
            task_id = instance.get("task_id")
            if isinstance(task_id, str):
                instances_by_task.setdefault(task_id, []).append(instance)
        bindings = base_suite._index(
            base_suite._items(bindings_document, "bindings", path=bindings_path),
            "binding_id",
            label="bindings",
        )
        guards = base_suite._index(
            base_suite._items(guards_document, "guards", path=guards_path),
            "guard_id",
            label="guards",
        )
        sources = base_suite._index(
            base_suite._items(sources_document, "sources", path=sources_path),
            "source_id",
            label="sources",
        )

        selected_tasks: list[dict[str, Any]] = []
        for task_id in selected_task_ids:
            task = tasks.get(task_id)
            task_instances = instances_by_task.get(task_id, [])
            if task is None or len(task_instances) != 1:
                raise ValueError(
                    f"selected task {task_id!r} must resolve to one instance"
                )
            snapshot = base_suite._task_snapshot(
                package_root=package_root,
                task=task,
                instance=task_instances[0],
                bindings=bindings,
                guards=guards,
                sources=sources,
            )
            snapshot["replicate_inputs"] = [
                item
                for item in snapshot["replicate_inputs"]
                if item.get("replicate_id") == "R1"
            ]
            snapshot["protocol_lineage"]["corrected_audit_replicate_count"] = 1
            selected_tasks.append(snapshot)
            if snapshot["protocol_lineage"]["source_protocol_repetitions"] > 1:
                limitations.append(
                    {
                        "robot_configuration_id": robot_id,
                        "task_id": task_id,
                        "kind": "source_protocol_repetition_coverage",
                        "note": snapshot["protocol_lineage"]["coverage_note"],
                    }
                )

        robot_suites.append(
            {
                "robot_configuration_id": robot_id,
                "package_version": version,
                "task_snapshot_id": snapshot_id,
                "canonical_source_paths": {
                    "catalog": _relative(catalog_path),
                    "instances": _relative(instances_path),
                    "bindings": _relative(bindings_path),
                    "guards": _relative(guards_path),
                    "sources": _relative(sources_path),
                },
                "tasks": selected_tasks,
            }
        )

    return {
        "artifact_type": "b2_corrected_r1_task_suite",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "source_formal_authority": {
            "document_id": "AA2-B2",
            "revision": "0.1.4",
            "formal_inputs_modified": False,
        },
        "formal_episode": False,
        "task_count": 10,
        "replicate_plan": {"replicate_ids": ["R1"]},
        "robot_suites": robot_suites,
        "limitations": limitations,
    }


def _reference_selection(task_suite: dict[str, Any]) -> dict[str, Any]:
    by_robot = {
        item["robot_configuration_id"]: item
        for item in task_suite["robot_suites"]
    }
    robots: list[dict[str, Any]] = []
    for robot_id, version, _tasks in SELECTIONS:
        package_root = _package_root(robot_id, version)
        resolved_dir = HERE / "reference/resolved" / robot_id
        robots.append(
            {
                "robot_configuration_id": robot_id,
                "package_root": _relative(package_root),
                "driver_path": _relative(
                    package_root / "reference/fixed_capability_driver.py"
                ),
                "capability_design_path": _relative(
                    resolved_dir / "capability_design.json"
                ),
                "package_version": version,
                "task_snapshot_id": by_robot[robot_id]["task_snapshot_id"],
                "condition": "skeleton-assisted",
            }
        )
    return {
        "artifact_type": "b2_corrected_r1_reference_selection",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "robots": robots,
    }


def _write_designs(task_suite: dict[str, Any]) -> None:
    by_robot = {
        item["robot_configuration_id"]: item
        for item in task_suite["robot_suites"]
    }
    for robot_id, version, _tasks in SELECTIONS:
        design = copy.deepcopy(base_suite._read_json(OLD_DESIGNS[robot_id]))
        design["package_version"] = version
        design["task_snapshot_id"] = by_robot[robot_id]["task_snapshot_id"]
        design["corrected_audit_note"] = (
            "Capability ABI and implementation are unchanged; only the isolated "
            "package identity is updated for the non-formal corrected-R1 audit."
        )
        _write_json(
            HERE / "reference/resolved" / robot_id / "capability_design.json",
            design,
        )


def _fresh_units() -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    for task_id in ("GO2-T02", "GO2-T03", "GO2-T06", "GO2-T16", "GO2-T17"):
        for model_id in MODELS:
            units.append(
                _unit("unitree-go2-stock-12dof", task_id, model_id, "fresh_corrected")
            )
    for task_id in ("mw_pick_place_wall", "mw_dial_turn"):
        for model_id in MODELS:
            units.append(
                _unit("robotstudio_so101", task_id, model_id, "fresh_corrected")
            )
    for task_id in ("mw_push_to_goal", "mw_sweep_into_goal"):
        unit = _unit("robotstudio_so101", task_id, "M2", "replacement")
        old_run = (
            "formal-b2-v014-batch001-company"
            if task_id == "mw_push_to_goal"
            else "formal-b2-v014-batch002-sweep-company"
        )
        old_unit = f"b2::robotstudio_so101::{task_id}::M2::R1"
        unit["original_unit_id"] = old_unit
        unit["original_incomplete_terminal_path"] = _relative(
            REPOSITORY_ROOT
            / "experiment/experiment1b_use/runs"
            / old_run
            / "terminals"
            / f"{old_unit.replace('::', '__')}.json"
        )
        units.append(unit)
    return units


def _unit(robot_id: str, task_id: str, model_id: str, origin: str) -> dict[str, str]:
    return {
        "unit_id": f"b2-corrected-r1::{robot_id}::{task_id}::{model_id}::R1",
        "robot_configuration_id": robot_id,
        "task_id": task_id,
        "model_id": model_id,
        "replicate_id": "R1",
        "execution_origin": origin,
    }


def _retained_units() -> list[dict[str, str]]:
    roots = {
        "mw_push_to_goal": "formal-b2-v014-batch001",
        "mw_sweep_into_goal": "formal-b2-v014-batch002-sweep",
        "mw_pick_place": "formal-b2-v014-batch003-pick-place",
    }
    retained: list[dict[str, str]] = []
    for task_id in ("mw_push_to_goal", "mw_sweep_into_goal"):
        stem = roots[task_id]
        for model_id in ("M1", "M3", "M4", "M5", "M6", "M8"):
            suffix = "m5" if model_id == "M5" else "company"
            run = f"{stem}-{suffix}"
            retained.append(_retained_unit(task_id, model_id, run))
    for model_id in MODELS:
        run = (
            "formal-b2-v014-batch003-pick-place-m5-retry1"
            if model_id == "M5"
            else "formal-b2-v014-batch003-pick-place-company"
        )
        retained.append(_retained_unit("mw_pick_place", model_id, run))
    return retained


def _retained_unit(task_id: str, model_id: str, run: str) -> dict[str, str]:
    old_unit = f"b2::robotstudio_so101::{task_id}::{model_id}::R1"
    safe = old_unit.replace("::", "__")
    terminal = (
        REPOSITORY_ROOT
        / "experiment/experiment1b_use/runs"
        / run
        / "terminals"
        / f"{safe}.json"
    )
    return {
        **_unit("robotstudio_so101", task_id, model_id, "retained_rejudged"),
        "original_unit_id": old_unit,
        "original_terminal_path": _relative(terminal),
    }


def _manifest(selection: dict[str, Any]) -> dict[str, Any]:
    fresh = _fresh_units()
    retained = _retained_units()
    if len(fresh) != 51 or len(retained) != 19:
        raise AssertionError("corrected R1 unit split is not 51 fresh plus 19 retained")
    return {
        "artifact_type": "b2_corrected_r1_manifest",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "formal_episode": False,
        "formal_denominator_entry": False,
        "models": list(MODELS),
        "replicate_id": "R1",
        "corrected_r1_units": 70,
        "fresh_execution_units": 51,
        "retained_rejudication_units": 19,
        "execution_origin_counts": {
            "retained_rejudged": 19,
            "fresh_corrected": 49,
            "replacement": 2,
        },
        "old_formal_plan_unchanged": 210,
        "paths": {
            "task_suite": _relative(HERE / "task_suite.json"),
            "provider_manifest": _relative(OLD_PROVIDER_MANIFEST),
            "reference_selection": _relative(HERE / "reference/selection.json"),
            "positive_control_index": _relative(
                CORRECTED_ROOT / "runs/positive-controls/positive_control_index.json"
            ),
        },
        "reference_selection": selection,
        "fresh_units": fresh,
        "retained_units": retained,
        "dispatch_order": [
            "GO2-T02",
            "GO2-T03",
            "GO2-T06",
            "GO2-T16",
            "GO2-T17",
            "mw_pick_place_wall",
            "mw_dial_turn",
            "M2_REPLACEMENTS",
        ],
        "retry_policy": {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        },
    }


def build() -> None:
    task_suite = _task_suite()
    _write_json(HERE / "task_suite.json", task_suite)
    _write_designs(task_suite)
    selection = _reference_selection(task_suite)
    _write_json(HERE / "reference/selection.json", selection)
    manifest = _manifest(selection)
    _write_json(HERE / "manifest.json", manifest)


if __name__ == "__main__":
    build()
    print(_relative(HERE / "manifest.json"))
