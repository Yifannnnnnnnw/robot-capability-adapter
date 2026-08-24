#!/usr/bin/env python3
"""Build the isolated corrected-R123-v2 task suite and execution manifest."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROFILE_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiment.experiment1b_use.config.task_suite import build_suite as base_suite  # noqa: E402


AUDIT_ID = "AA2-B2-CORRECTED-R123-V2"
AUDIT_REVISION = "1.0.0"
ARTIFACT_PREFIX = "b2_corrected_r123_v2"
MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
REPLICATES = ("R1", "R2", "R3")
TASK_ORDER = (
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
TASK_ROBOT = {
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
CHANGED_TASKS = ("GO2-T17", "mw_dial_turn")
SELECTIONS = (
    (
        "robotstudio_so101",
        "1.0.3",
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
        "1.0.2",
        ("GO2-T02", "GO2-T03", "GO2-T06", "GO2-T16", "GO2-T17"),
    ),
)
DISPATCH_ORDER = (
    "R1/GO2-T17",
    "R1/mw_dial_turn",
    *tuple(
        f"{replicate_id}/{task_id}"
        for task_id in TASK_ORDER
        for replicate_id in ("R2", "R3")
    ),
)

SOURCE_R1_ROOT = REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r1"
SOURCE_R1_SUITE = SOURCE_R1_ROOT / "config/task_suite.json"
SOURCE_R1_AGGREGATE = SOURCE_R1_ROOT / "analysis/corrected_r1_aggregate.json"
PROVIDER_MANIFEST = (
    REPOSITORY_ROOT / "experiment/experiment1b_use/config/providers/manifest.json"
)
OLD_DESIGNS = {
    robot_id: SOURCE_R1_ROOT
    / "config/reference/resolved"
    / robot_id
    / "capability_design.json"
    for robot_id, _version, _tasks in SELECTIONS
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _render(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value), indent=2, ensure_ascii=False, allow_nan=False
    ) + "\n"


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPOSITORY_ROOT))


def _package_root(robot_id: str, version: str) -> Path:
    return REPOSITORY_ROOT / "autoadapter/libraries/robots" / robot_id / version


def _task_index(suite: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for robot in suite.get("robot_suites", []):
        if not isinstance(robot, Mapping):
            raise ValueError("robot suite must be an object")
        for task in robot.get("tasks", []):
            if not isinstance(task, Mapping):
                raise ValueError("task snapshot must be an object")
            task_id = task.get("task_id")
            if not isinstance(task_id, str) or task_id in result:
                raise ValueError("task suite contains an invalid or duplicate task")
            result[task_id] = task
    return result


def _without_replicate_id(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(value))
    copied.pop("replicate_id", None)
    return copied


def _assert_unchanged_task(
    task_id: str,
    current: Mapping[str, Any],
    source: Mapping[str, Any],
) -> None:
    stable_fields = (
        "task_id",
        "private_instance_id",
        "public_projection",
        "private_scoring_clauses",
        "private_clause_bindings",
        "private_measurement_bindings",
        "private_guards",
        "source_records",
        "episode_budget",
        "rendering",
    )
    for field in stable_fields:
        if current.get(field) != source.get(field):
            raise ValueError(f"unchanged task {task_id} changed {field}")
    source_inputs = source.get("replicate_inputs")
    current_inputs = current.get("replicate_inputs")
    if (
        not isinstance(source_inputs, list)
        or len(source_inputs) != 1
        or not isinstance(source_inputs[0], Mapping)
        or not isinstance(current_inputs, list)
        or len(current_inputs) != 3
        or not all(isinstance(item, Mapping) for item in current_inputs)
    ):
        raise ValueError(f"task {task_id} has an invalid replicate declaration")
    normalized_source = _without_replicate_id(source_inputs[0])
    if any(_without_replicate_id(item) != normalized_source for item in current_inputs):
        raise ValueError(f"unchanged task {task_id} changed its fixed input")


def _assert_changed_definitions(tasks: Mapping[str, Mapping[str, Any]]) -> None:
    t17 = tasks["GO2-T17"]
    t17_parameters = t17["public_projection"]["request"]["task_parameters"]
    if t17_parameters != {
        "duration_s": 20.0,
        "path_waypoints_m": [
            [0.55, 0.25],
            [1.35, -0.25],
            [2.15, 0.25],
            [2.95, -0.25],
            [3.75, 0.25],
        ],
    }:
        raise ValueError("GO2-T17 does not contain the corrected five-point weave")
    t17_clauses = {
        clause.get("clause_id"): clause
        for clause in t17.get("private_scoring_clauses", [])
        if isinstance(clause, Mapping)
    }
    if (
        set(t17_clauses) != {"weave_order", "pole_clearance"}
        or t17_clauses["weave_order"].get("threshold") != 1.0
        or t17_clauses["pole_clearance"].get("threshold") != 0.1
        or any(
            "yaw" in json.dumps(clause, ensure_ascii=False).lower()
            for clause in t17_clauses.values()
        )
    ):
        raise ValueError("GO2-T17 scoring must remain weave/clearance only")

    dial = tasks["mw_dial_turn"]
    dial_parameters = dial["public_projection"]["request"]["task_parameters"]
    expected_x = {
        "contact_position": 0.347,
        "route_position": 0.347,
        "target_position": 0.350,
        "tool_target_position": 0.347,
    }
    if any(
        not isinstance(dial_parameters.get(field), list)
        or dial_parameters[field][0] != expected
        for field, expected in expected_x.items()
    ):
        raise ValueError("Dial public coordinates are not shifted inward by 0.05 m")
    dial_clauses = dial.get("private_scoring_clauses")
    if (
        not isinstance(dial_clauses, list)
        or len(dial_clauses) != 1
        or dial_clauses[0].get("clause_id") != "dial_target_distance"
        or dial_clauses[0].get("comparator") != "<="
        or dial_clauses[0].get("threshold") != 0.07
    ):
        raise ValueError("Dial terminal distance threshold changed")


def _task_suite() -> dict[str, Any]:
    source_suite = _read_object(SOURCE_R1_SUITE)
    if (
        source_suite.get("artifact_type") != "b2_corrected_r1_task_suite"
        or source_suite.get("audit_identity")
        != {"document_id": "AA2-B2-CORRECTED-R1", "revision": "1.0.0"}
    ):
        raise ValueError("source corrected-R1 task suite identity changed")
    source_tasks = _task_index(source_suite)
    robot_suites: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    built_tasks: dict[str, Mapping[str, Any]] = {}

    for robot_id, version, selected_task_ids in SELECTIONS:
        package_root = _package_root(robot_id, version)
        tasks_root = package_root / "tasks"
        paths = {
            "catalog": tasks_root / "catalog.json",
            "instances": tasks_root / "private/instances.json",
            "bindings": tasks_root / "private/bindings.json",
            "guards": tasks_root / "private/guards.json",
            "sources": tasks_root / "sources.json",
        }
        documents = {key: base_suite._read_json(path) for key, path in paths.items()}
        catalog = documents["catalog"]
        snapshot_id = catalog.get("snapshot_id")
        if (
            catalog.get("robot_configuration_id") != robot_id
            or catalog.get("package_version") != version
            or not isinstance(snapshot_id, str)
        ):
            raise ValueError(f"corrected package identity mismatch for {robot_id}")
        for key in ("instances", "bindings", "guards"):
            document = documents[key]
            if (
                document.get("robot_configuration_id") != robot_id
                or document.get("package_version") != version
                or document.get("task_snapshot_id") != snapshot_id
            ):
                raise ValueError(f"private package identity mismatch in {paths[key]}")

        catalog_tasks = base_suite._index(
            base_suite._items(catalog, "tasks", path=paths["catalog"]),
            "task_id",
            label="catalog",
        )
        instances_by_task: dict[str, list[dict[str, Any]]] = {}
        for instance in base_suite._items(
            documents["instances"], "instances", path=paths["instances"]
        ):
            task_id = instance.get("task_id")
            if isinstance(task_id, str):
                instances_by_task.setdefault(task_id, []).append(instance)
        bindings = base_suite._index(
            base_suite._items(
                documents["bindings"], "bindings", path=paths["bindings"]
            ),
            "binding_id",
            label="bindings",
        )
        guards = base_suite._index(
            base_suite._items(documents["guards"], "guards", path=paths["guards"]),
            "guard_id",
            label="guards",
        )
        sources = base_suite._index(
            base_suite._items(
                documents["sources"], "sources", path=paths["sources"]
            ),
            "source_id",
            label="sources",
        )

        selected_tasks: list[dict[str, Any]] = []
        for task_id in selected_task_ids:
            task = catalog_tasks.get(task_id)
            instances = instances_by_task.get(task_id, [])
            if task is None or len(instances) != 1:
                raise ValueError(f"selected task {task_id!r} must resolve once")
            snapshot = base_suite._task_snapshot(
                package_root=package_root,
                task=task,
                instance=instances[0],
                bindings=bindings,
                guards=guards,
                sources=sources,
            )
            if [item.get("replicate_id") for item in snapshot["replicate_inputs"]] != list(
                REPLICATES
            ):
                raise ValueError(f"task {task_id} does not expose R1-R3")
            lineage = snapshot.setdefault("protocol_lineage", {})
            if task_id in CHANGED_TASKS:
                lineage["corrected_r123_v2_definition_status"] = (
                    "replacement_new_definition"
                )
                lineage["semantic_equivalent_to_corrected_r1"] = False
            else:
                _assert_unchanged_task(task_id, snapshot, source_tasks[task_id])
                lineage["corrected_r123_v2_definition_status"] = (
                    "semantic_equivalent_to_corrected_r1"
                )
                lineage["semantic_equivalent_to_corrected_r1"] = True
            lineage["source_corrected_r1_audit_identity"] = {
                "document_id": "AA2-B2-CORRECTED-R1",
                "revision": "1.0.0",
            }
            selected_tasks.append(snapshot)
            built_tasks[task_id] = snapshot
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
                    key: _relative(path) for key, path in paths.items()
                },
                "tasks": selected_tasks,
            }
        )

    if set(built_tasks) != set(TASK_ORDER):
        raise ValueError("corrected-R123-v2 suite is not the fixed ten-task set")
    _assert_changed_definitions(built_tasks)
    return {
        "artifact_type": f"{ARTIFACT_PREFIX}_task_suite",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "source_formal_authority": {
            "document_id": "AA2-B2",
            "revision": "0.1.4",
            "formal_inputs_modified": False,
        },
        "formal_episode": False,
        "task_count": 10,
        "replicate_plan": {"replicate_ids": list(REPLICATES)},
        "definition_lineage": {
            "semantic_equivalent_task_ids": [
                task_id for task_id in TASK_ORDER if task_id not in CHANGED_TASKS
            ],
            "replacement_new_definition_task_ids": list(CHANGED_TASKS),
            "note": (
                "GO2-T17 and mw_dial_turn replace their corrected-R1 definitions; "
                "the other eight task definitions and fixed inputs are unchanged."
            ),
        },
        "robot_suites": robot_suites,
        "limitations": limitations,
    }


def _designs(task_suite: Mapping[str, Any]) -> dict[Path, dict[str, Any]]:
    robots = {
        item["robot_configuration_id"]: item
        for item in task_suite["robot_suites"]
    }
    result: dict[Path, dict[str, Any]] = {}
    for robot_id, version, _task_ids in SELECTIONS:
        design = copy.deepcopy(_read_object(OLD_DESIGNS[robot_id]))
        design["package_version"] = version
        design["task_snapshot_id"] = robots[robot_id]["task_snapshot_id"]
        design["corrected_audit_note"] = (
            "ReCAP capability ABI and fixed Driver implementation are unchanged. "
            "This isolated identity pins the corrected-R123-v2 package task snapshot."
        )
        result[
            HERE / "reference/resolved" / robot_id / "capability_design.json"
        ] = design
    return result


def _reference_selection(task_suite: Mapping[str, Any]) -> dict[str, Any]:
    robots_by_id = {
        item["robot_configuration_id"]: item
        for item in task_suite["robot_suites"]
    }
    robots: list[dict[str, Any]] = []
    for robot_id, version, _task_ids in SELECTIONS:
        package_root = _package_root(robot_id, version)
        robots.append(
            {
                "robot_configuration_id": robot_id,
                "package_root": _relative(package_root),
                "driver_path": _relative(
                    package_root / "reference/fixed_capability_driver.py"
                ),
                "capability_design_path": _relative(
                    HERE
                    / "reference/resolved"
                    / robot_id
                    / "capability_design.json"
                ),
                "package_version": version,
                "task_snapshot_id": robots_by_id[robot_id]["task_snapshot_id"],
                "condition": "skeleton-assisted",
            }
        )
    return {
        "artifact_type": f"{ARTIFACT_PREFIX}_reference_selection",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "robots": robots,
    }


def _source_r1_results() -> dict[tuple[str, str], Mapping[str, Any]]:
    aggregate = _read_object(SOURCE_R1_AGGREGATE)
    results = aggregate.get("results")
    if (
        aggregate.get("artifact_type") != "b2_corrected_r1_aggregate"
        or aggregate.get("audit_identity")
        != {"document_id": "AA2-B2-CORRECTED-R1", "revision": "1.0.0"}
        or aggregate.get("complete") is not True
        or not isinstance(results, list)
        or len(results) != 70
    ):
        raise ValueError("source corrected-R1 aggregate is not the complete 70 units")
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for result in results:
        if not isinstance(result, Mapping):
            raise ValueError("source corrected-R1 result is invalid")
        key = (str(result.get("task_id")), str(result.get("model_id")))
        if key in indexed:
            raise ValueError("source corrected-R1 result is duplicated")
        indexed[key] = result
    return indexed


def _unit(
    task_id: str,
    model_id: str,
    replicate_id: str,
    execution_origin: str,
) -> dict[str, Any]:
    robot_id = TASK_ROBOT[task_id]
    return {
        "unit_id": (
            f"b2-corrected-r123-v2::{robot_id}::{task_id}::"
            f"{model_id}::{replicate_id}"
        ),
        "robot_configuration_id": robot_id,
        "task_id": task_id,
        "model_id": model_id,
        "replicate_id": replicate_id,
        "execution_origin": execution_origin,
    }


def _execution_units() -> list[dict[str, Any]]:
    source = _source_r1_results()
    units: list[dict[str, Any]] = []
    for task_id in CHANGED_TASKS:
        for model_id in MODELS:
            unit = _unit(task_id, model_id, "R1", "replacement_new_definition")
            old = source[(task_id, model_id)]
            old_path = old.get("evidence_path")
            if not isinstance(old_path, str) or Path(old_path).is_absolute():
                raise ValueError(f"superseded terminal path is not repository-relative: {old_path}")
            resolved = (REPOSITORY_ROOT / old_path).resolve()
            resolved.relative_to(REPOSITORY_ROOT)
            if not resolved.is_file():
                raise ValueError(f"superseded terminal is absent: {resolved}")
            unit["superseded_unit_id"] = old.get("unit_id")
            unit["superseded_terminal_path"] = old_path
            units.append(unit)
    for task_id in TASK_ORDER:
        for replicate_id in ("R2", "R3"):
            for model_id in MODELS:
                units.append(
                    _unit(task_id, model_id, replicate_id, "fresh_corrected")
                )
    if len(units) != 154 or len({unit["unit_id"] for unit in units}) != 154:
        raise AssertionError("corrected-R123-v2 is not 154 unique execution units")
    return units


def _manifest(selection: Mapping[str, Any]) -> dict[str, Any]:
    units = _execution_units()
    return {
        "artifact_type": f"{ARTIFACT_PREFIX}_manifest",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "formal_episode": False,
        "formal_denominator_entry": False,
        "models": list(MODELS),
        "replicate_ids": list(REPLICATES),
        "execution_units": 154,
        "fresh_execution_units": 140,
        "replacement_units": 14,
        "retained_selected_units": 56,
        "selected_combined_plan": 210,
        "superseded_r1_units": 14,
        "execution_origin_counts": {
            "replacement_new_definition": 14,
            "fresh_corrected": 140,
        },
        "selected_composition": {
            "source_corrected_r1_semantic_equivalent": 56,
            "replacement_new_definition": 14,
            "fresh_corrected_r2_r3": 140,
        },
        "source_corrected_r1_filter": {
            "aggregate_path": _relative(SOURCE_R1_AGGREGATE),
            "retain_replicate_id": "R1",
            "exclude_task_ids": list(CHANGED_TASKS),
            "retained_units": 56,
            "superseded_units": 14,
        },
        "positive_control_gate": {
            "control_replicate_id": "R2",
            "covered_replicates": list(REPLICATES),
            "required_task_ids": list(TASK_ORDER),
            "required_pass_count": 10,
            "user_reacceptance_required_before_paid_dispatch": True,
        },
        "source_formal_plan_modified": False,
        "claim_boundary": (
            "This is a formal_episode=false corrected audit. Its selected 210 "
            "outcomes do not replace the source AA2-B2 formal record."
        ),
        "paths": {
            "task_suite": _relative(HERE / "task_suite.json"),
            "provider_manifest": _relative(PROVIDER_MANIFEST),
            "reference_selection": _relative(HERE / "reference/selection.json"),
            "positive_control_index": _relative(
                PROFILE_ROOT / "runs/positive-controls/positive_control_index.json"
            ),
            "source_corrected_r1_aggregate": _relative(SOURCE_R1_AGGREGATE),
        },
        "reference_selection": dict(selection),
        "fresh_units": units,
        "dispatch_order": list(DISPATCH_ORDER),
        "retry_policy": {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        },
    }


def _artifacts() -> dict[Path, dict[str, Any]]:
    task_suite = _task_suite()
    selection = _reference_selection(task_suite)
    artifacts: dict[Path, dict[str, Any]] = {
        HERE / "task_suite.json": task_suite,
        HERE / "reference/selection.json": selection,
        HERE / "manifest.json": _manifest(selection),
    }
    artifacts.update(_designs(task_suite))
    return artifacts


def build(*, check: bool = False) -> None:
    stale: list[str] = []
    for path, value in _artifacts().items():
        rendered = _render(value)
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                stale.append(_relative(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    if stale:
        raise RuntimeError(f"corrected-R123-v2 config is stale: {stale}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    build(check=args.check)
    print(_relative(HERE / "manifest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
