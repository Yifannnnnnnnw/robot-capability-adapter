"""Resolve external experiment manifests against AutoAdapter-Bench contracts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BENCHMARK_ROOT.parent
AUTOADAPTER_ROOT = REPOSITORY_ROOT / "autoadapter"


class ManifestError(RuntimeError):
    """Raised when a benchmark recipe is structurally inconsistent."""


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(resolved)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load JSON manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"manifest must contain one JSON object: {path}")
    return value


def _reference(source: Path, value: Any, field: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{source}: {field} must be a non-empty path")
    path = (source.parent / value).resolve()
    return path, _load(path)


def _string_list(value: Any, field: str, source: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{source}: {field} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ManifestError(f"{source}: {field} must contain non-empty strings")
    result = [item.strip() for item in value]
    if len(result) != len(set(result)):
        raise ManifestError(f"{source}: {field} must not contain duplicates")
    return result


def _component_ids(
    recipe_path: Path,
    recipe: dict[str, Any],
    reference_field: str,
    ids_field: str,
) -> tuple[Path, dict[str, Any], list[str]]:
    path, component = _reference(recipe_path, recipe.get(reference_field), reference_field)
    return path, component, _string_list(component.get(ids_field), ids_field, path)


def _backbone_registry() -> dict[str, dict[str, Any]]:
    path = BENCHMARK_ROOT / "backbones" / "registry.json"
    registry = _load(path)
    entries = registry.get("backbones")
    if not isinstance(entries, list):
        raise ManifestError(f"{path}: backbones must be a list")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ManifestError(f"{path}: every backbone must have a string id")
        backbone_id = entry["id"]
        if backbone_id in result:
            raise ManifestError(f"{path}: duplicate backbone id {backbone_id}")
        result[backbone_id] = entry
    return result


def resolve_b1(recipe_path: Path) -> dict[str, Any]:
    recipe_path = recipe_path.resolve()
    recipe = _load(recipe_path)
    if recipe.get("track") != "B1":
        raise ManifestError(f"{recipe_path}: track must be B1")

    protocol_path, protocol = _reference(
        recipe_path, recipe.get("protocol"), "protocol"
    )
    if protocol.get("track") != "B1":
        raise ManifestError(f"{protocol_path}: protocol track must be B1")
    if protocol.get("run_task_demo") is not False:
        raise ManifestError(f"{protocol_path}: B1 must not run Task Demo")
    if recipe.get("run_task_demo") is not False:
        raise ManifestError(f"{recipe_path}: B1 recipe must not run Task Demo")
    if protocol.get("run_high_level_controller") is not False:
        raise ManifestError(f"{protocol_path}: B1 must not run a high-level controller")
    if recipe.get("run_high_level_controller") is not False:
        raise ManifestError(
            f"{recipe_path}: B1 recipe must not run a high-level controller"
        )

    _, _, backbone_ids = _component_ids(
        recipe_path, recipe, "backbone_set", "backbone_ids"
    )
    registry = _backbone_registry()
    unknown_backbones = sorted(set(backbone_ids) - set(registry))
    if unknown_backbones:
        raise ManifestError(
            f"{recipe_path}: unknown backbone ids: {', '.join(unknown_backbones)}"
        )

    _, _, replicate_ids = _component_ids(
        recipe_path, recipe, "replicate_set", "replicate_ids"
    )
    allowed_conditions = set(
        _string_list(
            protocol.get("allowed_generation_conditions"),
            "allowed_generation_conditions",
            protocol_path,
        )
    )
    coverage = recipe.get("condition_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise ManifestError(f"{recipe_path}: condition_coverage must be a list")

    units: list[dict[str, str]] = []
    condition_counts: Counter[str] = Counter()
    robot_ids: list[str] = []
    seen_robots: set[str] = set()
    for assignment in coverage:
        if not isinstance(assignment, dict):
            raise ManifestError(f"{recipe_path}: condition coverage must use objects")
        condition = assignment.get("condition")
        if condition not in allowed_conditions:
            raise ManifestError(f"{recipe_path}: unsupported condition {condition!r}")
        robot_path, robot_set = _reference(
            recipe_path, assignment.get("robot_set"), "condition robot_set"
        )
        assignment_robot_ids = _string_list(
            robot_set.get("robot_configuration_ids"),
            "robot_configuration_ids",
            robot_path,
        )
        for robot_id in assignment_robot_ids:
            if robot_id not in seen_robots:
                seen_robots.add(robot_id)
                robot_ids.append(robot_id)
            for backbone_id in backbone_ids:
                for replicate_id in replicate_ids:
                    pair_id = f"b1::{robot_id}::{backbone_id}::{replicate_id}"
                    unit_id = f"{pair_id}::{condition}"
                    units.append(
                        {
                            "unit_id": unit_id,
                            "condition_pair_id": pair_id,
                            "robot_configuration_id": robot_id,
                            "backbone_id": backbone_id,
                            "replicate_id": replicate_id,
                            "generation_condition": condition,
                        }
                    )
                    condition_counts[condition] += 1

    unit_ids = [unit["unit_id"] for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ManifestError(f"{recipe_path}: condition coverage creates duplicate units")
    expected = recipe.get("expected_generation_condition_replicates")
    if expected != len(units):
        raise ManifestError(
            f"{recipe_path}: expected {expected} B1 units but resolved {len(units)}"
        )
    max_attempts = protocol.get("max_driver_attempts_per_condition")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
        raise ManifestError(f"{protocol_path}: invalid max attempt count")
    expected_attempts = recipe.get("maximum_submitted_driver_attempts")
    if expected_attempts != len(units) * max_attempts:
        raise ManifestError(
            f"{recipe_path}: maximum submitted attempts do not match unit count"
        )

    blockers = list(recipe.get("blocked_reasons", []))
    fixed_bundle_value = recipe.get("fixed_validation_bundle_set")
    fixed_bundle_path: str | None = None
    if not fixed_bundle_value:
        blockers.append("fixed per-robot B1 validation bundle set is missing")
    else:
        bundle_path, _ = _reference(
            recipe_path,
            fixed_bundle_value,
            "fixed_validation_bundle_set",
        )
        fixed_bundle_path = _display_path(bundle_path)
    blockers = list(dict.fromkeys(str(item) for item in blockers))

    package_state = package_inventory(robot_ids)
    return {
        "experiment_id": recipe.get("experiment_id"),
        "recipe": _display_path(recipe_path),
        "protocol": _display_path(protocol_path),
        "structurally_valid": True,
        "unit_count": len(units),
        "condition_counts": dict(sorted(condition_counts.items())),
        "maximum_submitted_driver_attempts": expected_attempts,
        "robot_count": len(robot_ids),
        "backbone_count": len(backbone_ids),
        "replicate_count": len(replicate_ids),
        "fixed_validation_bundle_set": fixed_bundle_path,
        "package_state": package_state,
        "ready_to_expand": package_state["all_units_runnable"] and not blockers,
        "blockers": blockers,
        "units": units,
    }


def package_inventory(robot_ids: list[str]) -> dict[str, Any]:
    index_path = AUTOADAPTER_ROOT / "libraries" / "robots" / "index.json"
    index = _load(index_path)
    indexed = index.get("robots")
    if not isinstance(indexed, dict):
        raise ManifestError(f"{index_path}: robots must be an object")

    package_versions: dict[str, list[str]] = {}
    missing_packages: list[str] = []
    for robot_id in robot_ids:
        root = AUTOADAPTER_ROOT / "libraries" / "robots" / robot_id
        versions = (
            sorted(path.name for path in root.iterdir() if path.is_dir())
            if root.is_dir()
            else []
        )
        package_versions[robot_id] = versions
        if not versions:
            missing_packages.append(robot_id)

    indexed_ids = sorted(robot_id for robot_id in robot_ids if robot_id in indexed)
    unindexed_ids = sorted(
        robot_id
        for robot_id in robot_ids
        if robot_id not in indexed and robot_id not in missing_packages
    )
    return {
        "package_versions": package_versions,
        "missing_package_ids": sorted(missing_packages),
        "runnable_index_ids": indexed_ids,
        "package_present_but_unindexed_ids": unindexed_ids,
        "all_units_runnable": not missing_packages and not unindexed_ids,
    }


def resolve_b2(recipe_path: Path) -> dict[str, Any]:
    recipe_path = recipe_path.resolve()
    recipe = _load(recipe_path)
    if recipe.get("track") != "B2":
        raise ManifestError(f"{recipe_path}: track must be B2")

    protocol_path, protocol = _reference(
        recipe_path, recipe.get("protocol"), "protocol"
    )
    if protocol.get("track") != "B2":
        raise ManifestError(f"{protocol_path}: protocol track must be B2")
    _, _, robot_ids = _component_ids(
        recipe_path, recipe, "robot_set", "robot_configuration_ids"
    )
    _, _, backbone_ids = _component_ids(
        recipe_path, recipe, "backbone_set", "backbone_ids"
    )
    task_path, task_set = _reference(recipe_path, recipe.get("task_set"), "task_set")
    controller_path, controller = _reference(
        recipe_path, recipe.get("high_level_controller"), "high_level_controller"
    )

    task_count = task_set.get("task_template_count")
    if not isinstance(task_count, int) or isinstance(task_count, bool) or task_count <= 0:
        raise ManifestError(f"{task_path}: task_template_count must be positive")
    seed_ids = _string_list(
        recipe.get("private_seed_ids"), "private_seed_ids", recipe_path
    )
    episodes = recipe.get("episodes_per_task_seed")
    if not isinstance(episodes, int) or isinstance(episodes, bool) or episodes <= 0:
        raise ManifestError(f"{recipe_path}: episodes_per_task_seed must be positive")
    planned = len(robot_ids) * len(backbone_ids) * task_count * len(seed_ids) * episodes
    expected = recipe.get("expected_controller_episodes_if_admitted")
    if planned != expected:
        raise ManifestError(
            f"{recipe_path}: expected {expected} B2 episodes but planned {planned}"
        )

    blockers = list(recipe.get("blocked_reasons", []))
    task_ids = task_set.get("task_template_ids")
    if not isinstance(task_ids, list) or len(task_ids) != task_count:
        blockers.append("B2 task template IDs are not frozen")
    if controller.get("audit_status") != "admitted":
        blockers.append(f"{controller.get('controller_id')} controller audit is incomplete")
    if controller.get("adapter_status") != "implemented":
        blockers.append(f"{controller.get('controller_id')} adapter is not implemented")
    if not recipe.get("fixed_driver_selection"):
        blockers.append("fixed driver selection is missing")
    if not recipe.get("fixed_interface_selection"):
        blockers.append("fixed capability-interface selection is missing")
    blockers = list(dict.fromkeys(str(item) for item in blockers))

    return {
        "experiment_id": recipe.get("experiment_id"),
        "recipe": _display_path(recipe_path),
        "protocol": _display_path(protocol_path),
        "controller": _display_path(controller_path),
        "structurally_valid": True,
        "planned_episode_count_if_admitted": planned,
        "robot_count": len(robot_ids),
        "backbone_count": len(backbone_ids),
        "task_template_count": task_count,
        "private_seed_count": len(seed_ids),
        "episodes_per_task_seed": episodes,
        "package_state": package_inventory(robot_ids),
        "ready_to_expand": not blockers,
        "blockers": blockers,
    }


def validation_summary(
    b1_recipe: Path | None = None,
    b2_recipe: Path | None = None,
) -> dict[str, Any]:
    if b1_recipe is None and b2_recipe is None:
        raise ManifestError("provide at least one external B1 or B2 manifest")
    result: dict[str, Any] = {
        "benchmark_root": str(BENCHMARK_ROOT),
        "structurally_valid": True,
    }
    readiness: list[bool] = []
    if b1_recipe is not None:
        b1 = resolve_b1(b1_recipe)
        result["b1"] = {key: value for key, value in b1.items() if key != "units"}
        readiness.append(bool(b1["ready_to_expand"]))
    if b2_recipe is not None:
        b2 = resolve_b2(b2_recipe)
        result["b2"] = b2
        readiness.append(bool(b2["ready_to_expand"]))
    result["ready_for_formal_execution"] = all(readiness)
    return result


def _write_json(value: Any, output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate external manifests")
    validate.add_argument("--b1-recipe", type=Path)
    validate.add_argument("--b2-recipe", type=Path)
    validate.add_argument("--require-ready", action="store_true")

    matrix = subparsers.add_parser("b1-matrix", help="emit resolved B1 units")
    matrix.add_argument("--recipe", type=Path, required=True)
    matrix.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            summary = validation_summary(args.b1_recipe, args.b2_recipe)
            _write_json(summary, None)
            if args.require_ready and not summary["ready_for_formal_execution"]:
                return 1
            return 0

        resolved = resolve_b1(args.recipe)
        _write_json(resolved, args.output)
        return 0
    except ManifestError as exc:
        _write_json(
            {
                "structurally_valid": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            },
            None,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
