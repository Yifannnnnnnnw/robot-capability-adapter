#!/usr/bin/env python3
"""Build the sealed ten-task B2 snapshot from the canonical robot packages."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[3]
OUTPUT_PATH = HERE / "task_suite.json"

REPLICATE_IDS = ("R1", "R2", "R3")
SELECTIONS = (
    (
        "robotstudio_so101",
        (
            "mw_push_to_goal",
            "mw_sweep_into_goal",
            "mw_pick_place",
            "mw_pick_place_wall",
            "mw_bin_picking",
        ),
    ),
    (
        "unitree-go2-stock-12dof",
        ("GO2-T02", "GO2-T03", "GO2-T06", "GO2-T16", "GO2-T17"),
    ),
)

_BUDGET_FIELDS = ("timeout_sim_s", "max_steps", "sample_hz")
_RENDER_FIELDS = ("video_fps", "video_width", "video_height", "camera")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _render_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _items(document: Mapping[str, Any], field: str, *, path: Path) -> list[dict[str, Any]]:
    values = document.get(field)
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ValueError(f"{path} must contain a {field} object list")
    return values


def _index(
    values: list[dict[str, Any]], key: str, *, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        identifier = value.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"{label} contains an invalid or duplicate {key}")
        result[identifier] = value
    return result


def _seed_fields(value: Any, *, path: str = "instance") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "seed" in str(key).lower():
                found.append(child_path)
            found.extend(_seed_fields(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_seed_fields(child, path=f"{path}[{index}]"))
    return found


def _required_task_parameters(task: Mapping[str, Any]) -> set[str]:
    schema = task.get("invocation_schema")
    if not isinstance(schema, Mapping):
        raise ValueError(f"task {task.get('task_id')!r} has no invocation_schema")
    request_schema = schema.get("request")
    if not isinstance(request_schema, Mapping):
        raise ValueError(f"task {task.get('task_id')!r} has no request schema")
    parameters = request_schema.get("task_parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError(f"task {task.get('task_id')!r} has no task_parameters schema")
    required = parameters.get("required")
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise ValueError(f"task {task.get('task_id')!r} has invalid required parameters")
    return set(required)


def _public_projection(
    task: Mapping[str, Any], instance: Mapping[str, Any]
) -> dict[str, Any]:
    public_arguments = instance.get("public_arguments")
    if not isinstance(public_arguments, Mapping):
        raise ValueError(f"instance {instance.get('instance_id')!r} has no public_arguments")
    request = public_arguments.get("request")
    task_id = task.get("task_id")
    if not isinstance(request, Mapping) or request.get("task_id") != task_id:
        raise ValueError(f"task {task_id!r} has an incompatible public request")
    parameters = request.get("task_parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError(f"task {task_id!r} public request has no task_parameters")
    missing = _required_task_parameters(task) - set(parameters)
    if missing:
        raise ValueError(f"task {task_id!r} public request is missing {sorted(missing)}")

    description = task.get("description")
    name = task.get("name")
    scene_assumptions = task.get("scene_assumptions")
    invocation_schema = task.get("invocation_schema")
    if (
        not isinstance(name, str)
        or not isinstance(description, str)
        or not isinstance(scene_assumptions, list)
        or not isinstance(invocation_schema, Mapping)
    ):
        raise ValueError(f"task {task_id!r} has an incomplete public projection")
    return {
        "task_id": task_id,
        "name": name,
        "objective": description,
        "scene_context": copy.deepcopy(scene_assumptions),
        "request": copy.deepcopy(dict(request)),
        "request_schema": copy.deepcopy(dict(invocation_schema)),
    }


def _task_snapshot(
    *,
    package_root: Path,
    task: Mapping[str, Any],
    instance: Mapping[str, Any],
    bindings: Mapping[str, Mapping[str, Any]],
    guards: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    instance_id = instance.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise ValueError(f"task {task_id!r} has no private instance ID")

    scene_entrypoint = instance.get("scene_entrypoint")
    if not isinstance(scene_entrypoint, str):
        raise ValueError(f"instance {instance_id!r} has no scene_entrypoint")
    scene_path = (package_root / scene_entrypoint).resolve()
    try:
        scene_path.relative_to((package_root / "assets").resolve())
    except ValueError as exc:
        raise ValueError(f"instance {instance_id!r} scene escapes package assets") from exc
    if not scene_path.is_file():
        raise ValueError(f"instance {instance_id!r} scene does not exist")

    reset = instance.get("reset")
    if not isinstance(reset, Mapping):
        raise ValueError(f"instance {instance_id!r} has no reset")
    seed_fields = _seed_fields(instance)
    if seed_fields:
        raise ValueError(
            f"instance {instance_id!r} declares seed fields requiring an explicit B2 map: "
            + ", ".join(seed_fields)
        )

    scoring = task.get("scoring")
    clause_bindings = instance.get("clause_bindings")
    if not isinstance(scoring, list) or not scoring or not isinstance(clause_bindings, Mapping):
        raise ValueError(f"task {task_id!r} has incomplete scoring or bindings")
    selected_bindings: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    clause_ids: set[str] = set()
    for clause in scoring:
        if not isinstance(clause, Mapping):
            raise ValueError(f"task {task_id!r} contains an invalid scoring clause")
        clause_id = clause.get("clause_id")
        if not isinstance(clause_id, str) or clause_id in clause_ids:
            raise ValueError(f"task {task_id!r} contains an invalid clause_id")
        clause_ids.add(clause_id)
        binding_id = clause_bindings.get(clause_id)
        binding = bindings.get(str(binding_id))
        if binding is None:
            raise ValueError(f"task {task_id!r} lacks binding for {clause_id!r}")
        if binding.get("metric") != clause.get("metric") or binding.get("unit") != clause.get("unit"):
            raise ValueError(f"task {task_id!r} has an incompatible binding for {clause_id!r}")
        selected_bindings.append(copy.deepcopy(dict(binding)))
        refs = clause.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"task {task_id!r} clause {clause_id!r} has no source lineage")
        for reference in refs:
            if not isinstance(reference, Mapping):
                raise ValueError(f"task {task_id!r} has an invalid source reference")
            source_id = reference.get("source_id")
            if not isinstance(source_id, str) or source_id not in sources:
                raise ValueError(f"task {task_id!r} references unknown source {source_id!r}")
            source_ids.add(source_id)

    guard_ids = instance.get("guard_ids")
    if not isinstance(guard_ids, list) or not guard_ids:
        raise ValueError(f"instance {instance_id!r} has no guards")
    try:
        selected_guards = [copy.deepcopy(dict(guards[str(guard_id)])) for guard_id in guard_ids]
    except KeyError as exc:
        raise ValueError(f"instance {instance_id!r} references unknown guard {exc.args[0]!r}") from exc

    source_repetitions = instance.get("repetitions", 1)
    if (
        not isinstance(source_repetitions, int)
        or isinstance(source_repetitions, bool)
        or source_repetitions <= 0
    ):
        raise ValueError(f"instance {instance_id!r} has invalid repetitions")
    variants = instance.get("repetition_variants")
    if variants is not None:
        raise ValueError(
            f"instance {instance_id!r} has repetition variants requiring an explicit B2 mapping"
        )

    budget: dict[str, Any] = {}
    rendering: dict[str, Any] = {
        "enabled": True,
        "continuous_episode_video_required": True,
    }
    for field in _BUDGET_FIELDS:
        if field not in instance:
            raise ValueError(f"instance {instance_id!r} lacks budget field {field}")
        budget[field] = copy.deepcopy(instance[field])
    for field in _RENDER_FIELDS:
        if field not in instance:
            raise ValueError(f"instance {instance_id!r} lacks rendering field {field}")
        rendering[field] = copy.deepcopy(instance[field])

    replicate_inputs = [
        {
            "replicate_id": replicate_id,
            "scene_entrypoint": scene_entrypoint,
            "reset": copy.deepcopy(dict(reset)),
            "reset_seed": None,
            "reset_seed_applied": False,
        }
        for replicate_id in REPLICATE_IDS
    ]
    coverage = (
        "The canonical package instance declares one trial; each B2 replicate reuses its exact "
        "scene and reset in a fresh episode."
        if source_repetitions == 1
        else (
            f"The canonical package instance declares {source_repetitions} trials but no "
            "repetition variants or reset-seed mechanism. Each B2 replicate evaluates one "
            "fresh, single-reset per-trial episode; this does not reproduce the source "
            f"{source_repetitions}-trial protocol aggregate."
        )
    )

    return {
        "task_id": task_id,
        "private_instance_id": instance_id,
        "public_projection": _public_projection(task, instance),
        "private_scoring_clauses": copy.deepcopy(scoring),
        "private_clause_bindings": copy.deepcopy(dict(clause_bindings)),
        "private_measurement_bindings": selected_bindings,
        "private_guards": selected_guards,
        "source_records": [copy.deepcopy(dict(sources[source_id])) for source_id in sorted(source_ids)],
        "episode_budget": budget,
        "rendering": rendering,
        "replicate_inputs": replicate_inputs,
        "protocol_lineage": {
            "source_protocol_repetitions": source_repetitions,
            "canonical_repetition_variants_available": 0,
            "b2_episode_reset_count": 1,
            "b2_clause_evaluations_per_episode": 1,
            "coverage_note": coverage,
        },
    }


def resolved_suite() -> dict[str, Any]:
    robot_suites: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    package_base = REPOSITORY_ROOT / "autoadapter" / "libraries" / "robots"
    for robot_id, selected_task_ids in SELECTIONS:
        package_root = package_base / robot_id / "1.0.0"
        tasks_root = package_root / "tasks"
        catalog_path = tasks_root / "catalog.json"
        instances_path = tasks_root / "private" / "instances.json"
        bindings_path = tasks_root / "private" / "bindings.json"
        guards_path = tasks_root / "private" / "guards.json"
        sources_path = tasks_root / "sources.json"

        catalog = _read_json(catalog_path)
        instances_document = _read_json(instances_path)
        bindings_document = _read_json(bindings_path)
        guards_document = _read_json(guards_path)
        sources_document = _read_json(sources_path)
        if catalog.get("robot_configuration_id") != robot_id:
            raise ValueError(f"catalog robot mismatch for {robot_id}")
        package_version = catalog.get("package_version")
        snapshot_id = catalog.get("snapshot_id")
        if package_version != "1.0.0" or not isinstance(snapshot_id, str):
            raise ValueError(f"catalog identity mismatch for {robot_id}")
        for document, path in (
            (instances_document, instances_path),
            (bindings_document, bindings_path),
            (guards_document, guards_path),
        ):
            if (
                document.get("robot_configuration_id") != robot_id
                or document.get("package_version") != package_version
                or document.get("task_snapshot_id") != snapshot_id
            ):
                raise ValueError(f"private package identity mismatch in {path}")

        tasks = _index(_items(catalog, "tasks", path=catalog_path), "task_id", label="catalog")
        instances_by_task: dict[str, list[dict[str, Any]]] = {}
        for instance in _items(instances_document, "instances", path=instances_path):
            task_id = instance.get("task_id")
            if isinstance(task_id, str):
                instances_by_task.setdefault(task_id, []).append(instance)
        bindings = _index(
            _items(bindings_document, "bindings", path=bindings_path),
            "binding_id",
            label="bindings",
        )
        guards = _index(
            _items(guards_document, "guards", path=guards_path),
            "guard_id",
            label="guards",
        )
        sources = _index(
            _items(sources_document, "sources", path=sources_path),
            "source_id",
            label="sources",
        )

        selected_tasks: list[dict[str, Any]] = []
        for task_id in selected_task_ids:
            task = tasks.get(task_id)
            task_instances = instances_by_task.get(task_id, [])
            if task is None or len(task_instances) != 1:
                raise ValueError(f"selected task {task_id!r} must resolve to exactly one instance")
            snapshot = _task_snapshot(
                package_root=package_root,
                task=task,
                instance=task_instances[0],
                bindings=bindings,
                guards=guards,
                sources=sources,
            )
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
                "package_version": package_version,
                "task_snapshot_id": snapshot_id,
                "canonical_source_paths": {
                    "catalog": str(catalog_path.relative_to(REPOSITORY_ROOT)),
                    "instances": str(instances_path.relative_to(REPOSITORY_ROOT)),
                    "bindings": str(bindings_path.relative_to(REPOSITORY_ROOT)),
                    "guards": str(guards_path.relative_to(REPOSITORY_ROOT)),
                    "sources": str(sources_path.relative_to(REPOSITORY_ROOT)),
                },
                "tasks": selected_tasks,
            }
        )

    return {
        "artifact_type": "b2_recap_task_suite",
        "schema_version": "1.0",
        "authority": {
            "document_id": "AA2-B2",
            "revision": "0.1.4",
            "path": "experiment/experiment1b_use/B2_RECAP_AUTHORITY.md",
        },
        "task_count": 10,
        "replicate_plan": {
            "replicate_ids": list(REPLICATE_IDS),
            "reset_seed_policy": (
                "The selected canonical instances declare no task-reset seed mechanism. "
                "R1-R3 retain distinct replicate IDs, reuse the exact canonical scene/reset, "
                "and record reset_seed=null with reset_seed_applied=false."
            ),
        },
        "public_projection_policy": (
            "Only each task's ID, name, objective, scene context, public request, and public "
            "invocation schema are projected to ReCAP. Scoring, sources, scenes, resets, "
            "bindings, guards, budgets, rendering, and replicate mappings remain trusted-private."
        ),
        "whole_suite_aggregation": {"kind": "all_selected_tasks"},
        "robot_suites": robot_suites,
        "limitations": limitations,
    }


def build(*, check: bool = False) -> None:
    rendered = _render_json(resolved_suite())
    if check:
        if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise RuntimeError(
                "task_suite.json is stale; run "
                "python experiment/experiment1b_use/config/task_suite/build_suite.py"
            )
        return
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if task_suite.json differs from a fresh mechanical build",
    )
    args = parser.parse_args()
    build(check=args.check)
    print("B2 task suite is current" if args.check else OUTPUT_PATH.relative_to(REPOSITORY_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
