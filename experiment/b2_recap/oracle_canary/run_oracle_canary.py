#!/usr/bin/env python3
"""Run the hidden scripted oracle through the persistent B2 path.

This is a diagnostic runner, not a formal episode dispatcher.  By default it
disables video, so even a physically correct diagnostic cannot clear the
AA2-B2 Section 7 scripted-oracle prerequisite.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from validate_oracle_plans import DESIGN_PATHS, PLANS_PATH, REPOSITORY_ROOT, validate


TASK_SUITE_PATH = REPOSITORY_ROOT / "experiment/b2_recap/task_suite/task_suite.json"
PACKAGE_ROOTS = {
    "robotstudio_so101": REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.0",
    "unitree-go2-stock-12dof": REPOSITORY_ROOT
    / "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0",
}
DRIVER_PATHS = {
    robot_id: package_root / "reference/fixed_capability_driver.py"
    for robot_id, package_root in PACKAGE_ROOTS.items()
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _leaf_response(leaf: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reasoning_summary": "Execute the next fixed scripted-oracle leaf.",
        "subtasks": [
            {
                "kind": "capability",
                "capability_name": str(leaf["capability_name"]),
                "request": copy.deepcopy(dict(leaf["request"])),
            }
        ],
    }


class ScriptedOracleModel:
    """Deterministically transports one validated ordered leaf sequence."""

    def __init__(self, leaves: Sequence[Mapping[str, Any]]) -> None:
        self._responses = [_leaf_response(leaf) for leaf in leaves]
        self._responses.append(
            {"reasoning_summary": "Scripted oracle sequence complete.", "subtasks": []}
        )
        self.call_count = 0

    def generate_recap_json(self, **_: Any) -> dict[str, Any]:
        if self.call_count >= len(self._responses):
            raise RuntimeError("scripted oracle received an unexpected model call")
        response = copy.deepcopy(self._responses[self.call_count])
        self.call_count += 1
        return response


def _task_index(suite: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(robot["robot_configuration_id"]), str(task["task_id"])): task
        for robot in suite["robot_suites"]
        for task in robot["tasks"]
    }


def _summary(
    *,
    plan: Mapping[str, Any],
    session: Mapping[str, Any],
    harness: Mapping[str, Any],
) -> dict[str, Any]:
    controller = session.get("controller")
    worker = session.get("worker")
    controller_value = controller if isinstance(controller, Mapping) else {}
    worker_value = worker if isinstance(worker, Mapping) else {}
    return {
        "robot_configuration_id": plan["robot_configuration_id"],
        "task_id": plan["task_id"],
        "diagnostic_status": "EXECUTED",
        "controller_status": controller_value.get("status"),
        "planned_leaf_count": len(plan["ordered_leaves"]),
        "executed_capability_calls": controller_value.get("capability_calls"),
        "worker_completed": worker_value.get("worker_completed"),
        "task_metric_passed": harness.get("task_metric_passed"),
        "physical_execution_passed": harness.get("physical_execution_passed"),
        "physical_integrity_passed": harness.get("physical_integrity_passed"),
        "video_complete": harness.get("video_complete"),
        "independent_harness_verdict": harness.get("physical_harness_verdict"),
        "formal_oracle_prerequisite_cleared": False,
    }


def run(*, only_task: str | None, wall_timeout_s: float) -> dict[str, Any]:
    validate()
    source_root = REPOSITORY_ROOT / "autoadapter/src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from autoadapter2.b2.recap import RecapBudgets
    from autoadapter2.b2.session_runner import (
        RecapWorkerSessionConfig,
        run_recap_worker_session,
    )
    from autoadapter2.b2.task_harness import evaluate_b2_task_harness
    from autoadapter2.libraries import load_robot_package

    oracle = _read_object(PLANS_PATH)
    suite = _read_object(TASK_SUITE_PATH)
    tasks = _task_index(suite)
    results: list[dict[str, Any]] = []
    selected = [
        plan
        for plan in oracle["plans"]
        if only_task is None or plan["task_id"] == only_task
    ]
    if not selected:
        raise ValueError(f"unknown oracle task {only_task!r}")

    packages = {}
    designs = {}
    for plan in selected:
        robot_id = str(plan["robot_configuration_id"])
        task_id = str(plan["task_id"])
        task = tasks[(robot_id, task_id)]
        leaves = plan["ordered_leaves"]
        model = ScriptedOracleModel(leaves)
        try:
            if robot_id not in packages:
                packages[robot_id] = load_robot_package(PACKAGE_ROOTS[robot_id])
                designs[robot_id] = _read_object(DESIGN_PATHS[robot_id])
            package = packages[robot_id]
            design = designs[robot_id]
            replicate = next(
                item
                for item in task["replicate_inputs"]
                if item["replicate_id"] == "R1"
            )
            session = run_recap_worker_session(
                config=RecapWorkerSessionConfig(
                    driver_path=DRIVER_PATHS[robot_id],
                    scene_path=package.root / replicate["scene_entrypoint"],
                    robot_configuration_id=robot_id,
                    reset=replicate["reset"],
                    max_steps=int(task["episode_budget"]["max_steps"]),
                    max_sim_time_s=float(task["episode_budget"]["timeout_sim_s"]),
                    sample_hz=float(task["episode_budget"]["sample_hz"]),
                    wall_timeout_s=wall_timeout_s,
                    render={"enabled": False},
                ),
                capability_design=design,
                public_task=task["public_projection"],
                model=model,
                budgets=RecapBudgets(
                    max_model_calls=len(leaves) + 1,
                    max_capability_calls=len(leaves),
                    max_depth=1,
                    max_subtasks_per_plan=1,
                    max_invalid_outputs=1,
                    max_history_chars=100_000,
                ),
            )
            harness = evaluate_b2_task_harness(
                package=package,
                task_suite_path=TASK_SUITE_PATH,
                instance_id=str(task["private_instance_id"]),
                replicate_id="R1",
                session_result=session,
            )
            results.append(_summary(plan=plan, session=session, harness=harness))
        except Exception as exc:
            results.append(
                {
                    "robot_configuration_id": robot_id,
                    "task_id": task_id,
                    "diagnostic_status": "ERROR",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                    "formal_oracle_prerequisite_cleared": False,
                }
            )

    return {
        "artifact_type": "b2_recap_oracle_no_video_diagnostic",
        "formal_episode": False,
        "video_enabled": False,
        "formal_oracle_prerequisite_cleared": False,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="run one task ID instead of all ten")
    parser.add_argument("--wall-timeout-s", type=float, default=300.0)
    args = parser.parse_args()
    print(
        json.dumps(
            run(only_task=args.task, wall_timeout_s=args.wall_timeout_s),
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
