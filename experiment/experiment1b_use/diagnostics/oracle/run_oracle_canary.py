#!/usr/bin/env python3
"""Run the hidden scripted oracle through the fixed B2 diagnostic episode path.

This legacy optional diagnostic never enters the formal 210-episode
denominator and is not an AA2-B2 formal-execution prerequisite.
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


TASK_SUITE_PATH = REPOSITORY_ROOT / "experiment/experiment1b_use/config/task_suite/task_suite.json"
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


def _summary(
    *,
    plan: Mapping[str, Any],
    episode: Mapping[str, Any],
) -> dict[str, Any]:
    controller = episode.get("controller")
    worker = episode.get("worker")
    harness = episode.get("harness")
    controller_value = controller if isinstance(controller, Mapping) else {}
    worker_value = worker if isinstance(worker, Mapping) else {}
    harness_value = harness if isinstance(harness, Mapping) else {}
    oracle_canary_passed = bool(
        harness_value.get("physical_harness_verdict") == "PASS"
        and harness_value.get("physical_integrity_passed") is True
        and harness_value.get("video_complete") is True
    )
    return {
        "robot_configuration_id": plan["robot_configuration_id"],
        "task_id": plan["task_id"],
        "diagnostic_status": "EXECUTED",
        "controller_status": controller_value.get("status"),
        "planned_leaf_count": len(plan["ordered_leaves"]),
        "executed_capability_calls": controller_value.get("capability_calls"),
        "worker_completed": worker_value.get("worker_completed"),
        "task_metric_passed": harness_value.get("task_metric_passed"),
        "physical_execution_passed": harness_value.get("physical_execution_passed"),
        "physical_integrity_passed": harness_value.get("physical_integrity_passed"),
        "video_complete": harness_value.get("video_complete"),
        "independent_harness_verdict": harness_value.get(
            "physical_harness_verdict"
        ),
        "oracle_canary_passed": oracle_canary_passed,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(
    *,
    only_task: str | None,
    wall_timeout_s: float,
    output_dir: Path,
    record_video: bool = True,
) -> dict[str, Any]:
    validate()
    source_root = REPOSITORY_ROOT / "autoadapter/src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from autoadapter2.b2.episode_runner import (
        B2DiagnosticEpisodeConfig,
        run_b2_diagnostic_episode,
    )
    from autoadapter2.b2.recap import RecapBudgets
    from autoadapter2.libraries import load_robot_package

    oracle = _read_object(PLANS_PATH)
    results: list[dict[str, Any]] = []
    selected = [
        plan
        for plan in oracle["plans"]
        if only_task is None or plan["task_id"] == only_task
    ]
    if not selected:
        raise ValueError(f"unknown oracle task {only_task!r}")
    output_root = output_dir.resolve()

    packages = {}
    for plan in selected:
        robot_id = str(plan["robot_configuration_id"])
        task_id = str(plan["task_id"])
        leaves = plan["ordered_leaves"]
        model = ScriptedOracleModel(leaves)
        task_output_dir = output_root / robot_id / task_id
        report_path = task_output_dir / "oracle_canary_report.json"
        episode: dict[str, Any] | None = None
        error: dict[str, str] | None = None
        try:
            if robot_id not in packages:
                packages[robot_id] = load_robot_package(PACKAGE_ROOTS[robot_id])
            package = packages[robot_id]
            episode = run_b2_diagnostic_episode(
                config=B2DiagnosticEpisodeConfig(
                    task_suite_path=TASK_SUITE_PATH,
                    robot_configuration_id=robot_id,
                    task_id=task_id,
                    replicate_id="R1",
                    driver_path=DRIVER_PATHS[robot_id],
                    capability_design_path=DESIGN_PATHS[robot_id],
                    output_dir=task_output_dir,
                    record_video=record_video,
                    wall_timeout_s=wall_timeout_s,
                ),
                package=package,
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
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            }

        if episode is None:
            summary = {
                "robot_configuration_id": robot_id,
                "task_id": task_id,
                "diagnostic_status": "ERROR",
                "planned_leaf_count": len(leaves),
                "oracle_canary_passed": False,
            }
        else:
            summary = _summary(plan=plan, episode=episode)
        summary["scripted_model_calls"] = model.call_count
        summary["report"] = str(report_path.relative_to(output_root))
        task_report = {
            "artifact_type": "b2_recap_scripted_oracle_task_canary",
            "formal_episode": False,
            "formal_denominator_entry": False,
            "video_requested": record_video,
            "robot_configuration_id": robot_id,
            "task_id": task_id,
            "replicate_id": "R1",
            "summary": summary,
            "error": error,
            "episode": episode,
        }
        _write_json(report_path, task_report)
        results.append(summary)

    complete_task_cohort = len(selected) == len(oracle["plans"]) == 10
    selected_canaries_passed = bool(results) and all(
        result.get("oracle_canary_passed") is True for result in results
    )
    diagnostic_cohort_passed = bool(
        record_video and complete_task_cohort and selected_canaries_passed
    )
    index = {
        "artifact_type": "b2_recap_scripted_oracle_canary_index",
        "formal_episode": False,
        "formal_denominator_entry": False,
        "oracle_plan_path": str(PLANS_PATH),
        "task_suite_path": str(TASK_SUITE_PATH),
        "video_enabled": record_video,
        "selected_task_count": len(selected),
        "required_task_count": len(oracle["plans"]),
        "complete_task_cohort": complete_task_cohort,
        "selected_canaries_passed": selected_canaries_passed,
        "diagnostic_cohort_passed": diagnostic_cohort_passed,
        "results": results,
    }
    _write_json(output_root / "oracle_canary_index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="run one task ID instead of all ten")
    parser.add_argument("--wall-timeout-s", type=float, default=300.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "experiment/experiment1b_use/runs/oracle-canary",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="legacy diagnostic only; video is required for a complete diagnostic",
    )
    args = parser.parse_args()
    report = run(
        only_task=args.task,
        wall_timeout_s=args.wall_timeout_s,
        output_dir=args.output,
        record_video=not args.no_video,
    )
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["diagnostic_cohort_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
