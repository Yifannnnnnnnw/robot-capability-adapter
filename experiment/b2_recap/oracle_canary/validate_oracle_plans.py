#!/usr/bin/env python3
"""Validate the hidden scripted oracle against the sealed B2 interfaces."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
PLANS_PATH = HERE / "oracle_plans.json"
TASK_SUITE_PATH = REPOSITORY_ROOT / "experiment/b2_recap/task_suite/task_suite.json"
DESIGN_PATHS = {
    "robotstudio_so101": REPOSITORY_ROOT
    / "experiment/b2_recap/reference_validation/resolved/robotstudio_so101/capability_design.json",
    "unitree-go2-stock-12dof": REPOSITORY_ROOT
    / "experiment/b2_recap/reference_validation/resolved/unitree-go2-stock-12dof/capability_design.json",
}

_FORBIDDEN_KEY_TOKENS = {
    "binding",
    "bindings",
    "criterion",
    "criteria",
    "guard",
    "guards",
    "macro",
    "private",
    "reset",
    "scoring",
    "threshold",
    "thresholds",
    "verdict",
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _task_index(task_suite: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    robot_suites = task_suite.get("robot_suites")
    if not isinstance(robot_suites, list):
        raise ValueError("sealed task suite has no robot_suites")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for robot in robot_suites:
        if not isinstance(robot, Mapping):
            raise ValueError("sealed task suite contains an invalid robot suite")
        robot_id = robot.get("robot_configuration_id")
        tasks = robot.get("tasks")
        if not isinstance(robot_id, str) or not isinstance(tasks, list):
            raise ValueError("sealed task suite robot identity is invalid")
        for task in tasks:
            if not isinstance(task, Mapping) or not isinstance(task.get("task_id"), str):
                raise ValueError("sealed task suite contains an invalid task")
            key = (robot_id, str(task["task_id"]))
            if key in result:
                raise ValueError(f"sealed task suite duplicates {key}")
            result[key] = task
    return result


def _assert_no_private_fields(value: Any, *, path: str = "oracle") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            tokens = set(key.lower().replace("-", "_").split("_"))
            if tokens & _FORBIDDEN_KEY_TOKENS:
                raise ValueError(f"{path}.{key} contains a forbidden oracle-plan field")
            _assert_no_private_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_private_fields(child, path=f"{path}[{index}]")


def validate(plans_path: Path = PLANS_PATH) -> dict[str, Any]:
    source_root = REPOSITORY_ROOT / "autoadapter/src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from autoadapter2.b2.capability_adapter import CapabilityAdapter

    oracle = _read_object(plans_path)
    if set(oracle) != {
        "artifact_type",
        "schema_version",
        "task_suite_identity",
        "capability_design_ids",
        "plans",
    }:
        raise ValueError("oracle plan artifact has unexpected top-level fields")
    if (
        oracle.get("artifact_type") != "b2_recap_scripted_oracle_plans"
        or oracle.get("schema_version") != "1.0"
    ):
        raise ValueError("oracle plan artifact identity is invalid")
    _assert_no_private_fields(oracle)

    task_suite = _read_object(TASK_SUITE_PATH)
    task_index = _task_index(task_suite)
    if len(task_index) != 10 or task_suite.get("task_count") != 10:
        raise ValueError("sealed task suite must contain exactly ten tasks")
    if oracle.get("task_suite_identity") != {
        "artifact_type": task_suite.get("artifact_type"),
        "schema_version": task_suite.get("schema_version"),
        "task_count": task_suite.get("task_count"),
    }:
        raise ValueError("oracle does not identify the sealed ten-task suite")

    design_ids = oracle.get("capability_design_ids")
    if not isinstance(design_ids, Mapping) or set(design_ids) != set(DESIGN_PATHS):
        raise ValueError("oracle capability-design identity map is invalid")
    adapters = {}
    for robot_id, path in DESIGN_PATHS.items():
        design = _read_object(path)
        if design_ids.get(robot_id) != design.get("capability_design_id"):
            raise ValueError(f"oracle capability design does not match {robot_id}")
        adapters[robot_id] = CapabilityAdapter(design, lambda _name, _request: {})

    raw_plans = oracle.get("plans")
    if not isinstance(raw_plans, list) or len(raw_plans) != 10:
        raise ValueError("oracle must contain exactly ten plans")
    seen: set[tuple[str, str]] = set()
    leaf_count = 0
    for index, plan in enumerate(raw_plans):
        if not isinstance(plan, Mapping) or set(plan) != {
            "robot_configuration_id",
            "task_id",
            "ordered_leaves",
        }:
            raise ValueError(f"plans[{index}] has invalid fields")
        robot_id = plan.get("robot_configuration_id")
        task_id = plan.get("task_id")
        if not isinstance(robot_id, str) or not isinstance(task_id, str):
            raise ValueError(f"plans[{index}] identity is invalid")
        key = (robot_id, task_id)
        if key not in task_index or key in seen:
            raise ValueError(f"oracle plan coverage is invalid for {key}")
        leaves = plan.get("ordered_leaves")
        if not isinstance(leaves, list) or not 1 <= len(leaves) <= 16:
            raise ValueError(f"oracle plan {key} must contain one to sixteen leaves")
        adapter = adapters[robot_id]
        for leaf_index, leaf in enumerate(leaves):
            if not isinstance(leaf, Mapping) or set(leaf) != {
                "capability_name",
                "request",
            }:
                raise ValueError(f"oracle plan {key} leaf {leaf_index} is invalid")
            capability_name = leaf.get("capability_name")
            request = leaf.get("request")
            if not isinstance(capability_name, str) or not isinstance(request, Mapping):
                raise ValueError(f"oracle plan {key} leaf {leaf_index} is invalid")
            if "task_id" in request or "task_parameters" in request:
                raise ValueError(f"oracle plan {key} contains a task-level macro request")
            adapter.validate_request(capability_name, request)
            leaf_count += 1
        seen.add(key)

    if seen != set(task_index):
        raise ValueError("oracle plan set does not cover the sealed task suite exactly")
    return {"task_count": len(seen), "leaf_count": leaf_count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=Path, default=PLANS_PATH)
    args = parser.parse_args()
    result = validate(args.plans.resolve())
    print(json.dumps({"status": "VALID", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
