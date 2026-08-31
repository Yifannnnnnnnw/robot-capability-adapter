#!/usr/bin/env python3
"""Run the fixed Exp1a reference Drivers as zero-model diagnostics.

This utility never constructs a model client and never writes into the formal
cell directory.  It loads the active package index and fixed validation bundle,
runs the package-private reference Driver through the real trusted Harness, and
records a diagnostic report beneath an explicitly supplied output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
AUTOADAPTER_ROOT = REPOSITORY_ROOT / "autoadapter"
AUTOADAPTER_SOURCE_ROOT = AUTOADAPTER_ROOT / "src"
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from autoadapter2.harness.runner import run_private_suite  # noqa: E402
from autoadapter2.libraries import load_indexed_robot_package  # noqa: E402
from experiment.experiment1a_generation.runtime.b1 import (  # noqa: E402
    fully_validated,
    load_fixed_bundle,
    resolve_experiment_manifest,
    validation_case_counts,
)


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _portable_paths(value: Any) -> Any:
    """Store repository-owned evidence paths without a workstation prefix."""

    if isinstance(value, dict):
        return {str(key): _portable_paths(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_portable_paths(child) for child in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(REPOSITORY_ROOT).as_posix()
            except (OSError, ValueError):
                return value
    return value


_TRIAL_SUMMARY_FIELDS = (
    "trial_id",
    "case_id",
    "capability_id",
    "source_clause_id",
    "method_invoked",
    "worker_completed",
    "controller_completed",
    "candidate_exception",
    "criterion_passed",
    "aggregation_passed",
    "aggregation_value",
    "temporal_passed",
    "physical_execution_passed",
    "physical_integrity_passed",
    "task_metric_passed",
    "trial_passed",
    "measurement_value",
    "measurement_error",
    "guard_outcomes",
    "video",
)


def _compact_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Drop frame-level telemetry while retaining every case verdict and video."""

    compact = dict(report)
    trials = report.get("trials")
    if isinstance(trials, list):
        compact["trials"] = [
            {key: trial[key] for key in _TRIAL_SUMMARY_FIELDS if key in trial}
            for trial in trials
            if isinstance(trial, dict)
        ]
    return compact


def run_reference_positive_control(
    *,
    robot_id: str,
    output_dir: str | Path,
    record_video: bool = True,
    wall_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Execute one active fixed-bundle reference control with no LLM calls."""

    manifest_path = EXPERIMENT_ROOT / "manifest.json"
    resolved = resolve_experiment_manifest(manifest_path)
    robot_ids = tuple(str(value) for value in resolved["robot_ids"])
    if robot_id not in robot_ids:
        raise ValueError(f"robot is not in the active Exp1a cohort: {robot_id}")
    package = load_indexed_robot_package(AUTOADAPTER_ROOT, robot_id)
    bundle = load_fixed_bundle(
        manifest_path,
        robot_ids,
        robot_id,
        package,
    )
    driver_path = package.root / "reference" / "fixed_capability_driver.py"
    if not driver_path.is_file():
        raise FileNotFoundError(f"fixed reference Driver is missing: {driver_path}")

    destination = Path(output_dir).resolve()
    report = run_private_suite(
        package=package,
        design=bundle.design,
        suite=bundle.suite,
        driver_path=driver_path,
        condition="skeleton-assisted",
        output_dir=destination / "harness",
        record_video=record_video,
        wall_timeout_s=wall_timeout_s,
        run_id=f"exp1a-reference-{robot_id}",
        attempt=0,
    )
    harness_aggregation_passed = bool(report.get("validation_passed"))
    strict_passed = fully_validated(
        bundle.suite,
        report,
        video_required=record_video,
    )
    case_counts = validation_case_counts(bundle.suite, report)
    report = {
        **report,
        "artifact_type": "experiment1a_reference_positive_control",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal": False,
        "denominator_entry": False,
        "model_calls": 0,
        "robot_configuration_id": robot_id,
        "package_version": package.package_version,
        "fixed_capability_interface_id": bundle.fixed_capability_interface_id,
        "fixed_capability_pass_standard_id": bundle.fixed_capability_pass_standard_id,
        "validation_suite_id": bundle.validation_suite_id,
        "reference_driver": str(driver_path),
        "harness_aggregation_passed": harness_aggregation_passed,
        "fully_validated": strict_passed,
        "validation_passed": strict_passed,
        "validation_case_counts": case_counts,
        "case_count": case_counts["total"],
        "passed_case_count": case_counts["passed"],
    }
    portable_report = _portable_paths(_compact_evidence(report))
    _write(destination / "reference_positive_control.json", portable_report)
    return portable_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--wall-timeout-s", type=float, default=120.0)
    arguments = parser.parse_args()
    report = run_reference_positive_control(
        robot_id=arguments.robot,
        output_dir=arguments.output,
        record_video=not arguments.no_video,
        wall_timeout_s=arguments.wall_timeout_s,
    )
    print(
        json.dumps(
            {
                "robot_configuration_id": report["robot_configuration_id"],
                "package_version": report["package_version"],
                "physical_validation_executed": report.get(
                    "physical_validation_executed"
                ),
                "validation_passed": report.get("validation_passed"),
                "fully_validated": report.get("fully_validated"),
                "video_complete": report.get("video_complete"),
                "case_count": report.get("case_count"),
            },
            sort_keys=True,
        )
    )
    return 0 if report.get("validation_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
