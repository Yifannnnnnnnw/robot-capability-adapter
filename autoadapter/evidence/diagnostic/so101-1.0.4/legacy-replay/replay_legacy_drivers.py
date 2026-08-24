#!/usr/bin/env python3
"""Replay unchanged historical SO-101 Driver submissions on package 1.0.4.

This is a diagnostic inventory and replay.  Its output is deliberately marked
non-formal and is never an Experiment 1 or Experiment 3 denominator input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
AUTOADAPTER_ROOT = REPOSITORY_ROOT / "autoadapter"
PACKAGE_ROOT = (
    AUTOADAPTER_ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.4"
)
BUNDLE_ROOT = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1a_generation"
    / "validation"
    / "fixed_validation_bundles"
    / "robotstudio_so101"
)
DESIGN_PATH = BUNDLE_ROOT / "capability_design.json"
SUITE_PATH = BUNDLE_ROOT / "capability_validation_suite.json"
SOURCE_AUDIT_PATH = (
    AUTOADAPTER_ROOT
    / "src"
    / "autoadapter2"
    / "driver_synthesis"
    / "source_check.py"
)
HARNESS_RUNNER_PATH = (
    AUTOADAPTER_ROOT / "src" / "autoadapter2" / "harness" / "runner.py"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "legacy_driver_replay.json"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository_path(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


def _walk_named(roots: Iterable[Path], filename: str) -> list[Path]:
    """Find records without descending into copied candidate workspaces."""

    found: list[Path] = []
    pruned = {
        ".git",
        "__pycache__",
        "workspace",
        "inputs",
        "frozen",
        "source",
        "probe-runtime",
        "staged",
    }
    for root in roots:
        if not root.exists():
            continue
        for directory, names, files in os.walk(root):
            names[:] = [name for name in names if name not in pruned]
            if filename in files:
                found.append(Path(directory) / filename)
    return sorted(found)


def _contains_submit_driver(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("submission_event") == "submit_driver":
            return True
        tool_names = value.get("tool_names")
        if isinstance(tool_names, list) and "submit_driver" in tool_names:
            return True
        return any(_contains_submit_driver(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_submit_driver(child) for child in value)
    return False


def _condition(value: str) -> str:
    return "skeleton-assisted" if "skeleton-assisted" in value else "from-scratch"


def _cell_identity(record: Mapping[str, Any], record_path: Path) -> dict[str, Any]:
    run_id = str(record.get("run_id") or record_path.parent.name)
    parts = run_id.split("::")
    return {
        "run_id": run_id,
        "model_id": parts[2] if len(parts) > 2 and parts[0] == "b1" else None,
        "replicate_id": parts[3] if len(parts) > 3 and parts[0] == "b1" else None,
        "condition": _condition(run_id + " " + record_path.as_posix()),
    }


def _discover_exp1_submissions() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    roots = (
        REPOSITORY_ROOT / "experiment" / "archive" / "runs" / "experiment1",
        REPOSITORY_ROOT / "experiment" / "experiment1a_generation" / "runs",
    )
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in _walk_named(roots, "cell_record.json"):
        try:
            record = _read_object(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        identity = str(record.get("run_id", ""))
        if (
            "robotstudio_so101" in identity
            or record.get("robot_configuration_id") == "robotstudio_so101"
            or "so101" in path.as_posix().lower()
        ):
            records.append((path, record))

    submissions: list[dict[str, Any]] = []
    records_with_submissions = 0
    distribution: Counter[int] = Counter()
    for record_path, record in records:
        identity = _cell_identity(record, record_path)
        actions = record.get("actions")
        submitted_actions = (
            [
                action
                for action in actions
                if isinstance(action, Mapping)
                and action.get("submission_event") == "submit_driver"
            ]
            if isinstance(actions, list)
            else []
        )
        distribution[len(submitted_actions)] += 1
        if submitted_actions:
            records_with_submissions += 1
        for action in submitted_actions:
            attempt = action.get("target_attempt")
            driver_path = (
                record_path.parent / "workspace" / f"attempt-{attempt}" / "driver.py"
            )
            submissions.append(
                {
                    "corpus": "exp1_cell_record",
                    "record_path": _repository_path(record_path),
                    **identity,
                    "attempt": attempt,
                    "driver_path": _repository_path(driver_path),
                    "artifact_located": driver_path.is_file(),
                }
            )
    inventory = {
        "record_count": len(records),
        "records_with_submissions": records_with_submissions,
        "records_without_submissions": len(records) - records_with_submissions,
        "submission_count_distribution": {
            str(key): distribution[key] for key in sorted(distribution)
        },
        "submission_count": len(submissions),
    }
    return submissions, inventory


def _discover_historical_demo_submissions() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cells_root = (
        AUTOADAPTER_ROOT
        / "runs"
        / "historical-demo3"
        / "deepseek-full-convergence-20260818T144502Z"
        / "cells"
        / "robotstudio_so101"
    )
    reports = sorted(cells_root.glob("*/cell_report.json"))
    submissions: list[dict[str, Any]] = []
    for report_path in reports:
        report = _read_object(report_path)
        condition = _condition(report_path.parent.name)
        attempts = report.get("attempts")
        if not isinstance(attempts, list):
            continue
        for attempt_record in attempts:
            if not isinstance(attempt_record, Mapping):
                continue
            if not bool(attempt_record.get("driver_generated")):
                continue
            if not _contains_submit_driver(attempt_record):
                continue
            attempt = attempt_record.get("attempt")
            driver_path = report_path.parent / f"attempt-{attempt}" / "driver.py"
            submissions.append(
                {
                    "corpus": "historical_demo3_cell_report",
                    "record_path": _repository_path(report_path),
                    "run_id": str(
                        report.get("run_id")
                        or _repository_path(report_path.parent)
                    ),
                    "model_id": "deepseek-v4-pro",
                    "replicate_id": None,
                    "condition": condition,
                    "attempt": attempt,
                    "driver_path": _repository_path(driver_path),
                    "artifact_located": driver_path.is_file(),
                }
            )
    return submissions, {
        "record_count": len(reports),
        "records_with_submissions": sum(
            any(item["record_path"] == _repository_path(path) for item in submissions)
            for path in reports
        ),
        "submission_count": len(submissions),
    }


def _discover_excluded_exp2() -> dict[str, Any]:
    roots = (AUTOADAPTER_ROOT / "runs" / "experiment2",)
    reports = [
        path
        for path in _walk_named(roots, "cell_report.json")
        if "robotstudio_so101" in path.as_posix()
    ]
    located = 0
    submitted = 0
    details: list[dict[str, Any]] = []
    for path in reports:
        report = _read_object(path)
        attempts = report.get("attempts")
        count = 0
        found = 0
        if isinstance(attempts, list):
            for item in attempts:
                if not isinstance(item, Mapping) or not _contains_submit_driver(item):
                    continue
                count += 1
                attempt = item.get("attempt")
                if (path.parent / f"attempt-{attempt}" / "driver.py").is_file():
                    found += 1
        submitted += count
        located += found
        details.append(
            {
                "record_path": _repository_path(path),
                "submission_count": count,
                "located_artifact_count": found,
            }
        )
    return {
        "reason": (
            "Exp2 is explicitly out of scope for this preparation round; its records "
            "were inventoried but no source audit or replay was executed."
        ),
        "record_count": len(reports),
        "submission_count": submitted,
        "located_artifact_count": located,
        "records": details,
    }


def _discover_auxiliary_route_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(
        (AUTOADAPTER_ROOT / "runs").glob("route-so101-*/route_report.json")
    ):
        report = _read_object(path)
        stages = report.get("stages")
        generate = stages.get("GENERATE") if isinstance(stages, Mapping) else None
        driver_value = generate.get("driver_path") if isinstance(generate, Mapping) else None
        driver_path = Path(driver_value) if isinstance(driver_value, str) else None
        if driver_path is not None and not driver_path.is_absolute():
            driver_path = REPOSITORY_ROOT / driver_path
        candidates.append(
            {
                "record_path": _repository_path(path),
                "run_id": report.get("run_id"),
                "route_completed": bool(report.get("route_completed")),
                "generate_completed": bool(
                    isinstance(generate, Mapping) and generate.get("completed")
                ),
                "driver_path": (
                    _repository_path(driver_path) if driver_path is not None else None
                ),
                "artifact_located": bool(driver_path is not None and driver_path.is_file()),
                "submission_counted": False,
                "reason": "route report is not a Driver-attempt submission record",
            }
        )
    return candidates


def _source_audit(submission: dict[str, Any], methods: tuple[str, ...]) -> dict[str, Any]:
    from autoadapter2.driver_synthesis import audit_driver_source

    if not submission["artifact_located"]:
        return {**submission, "status": "missing_artifact", "source_sha256": None}
    path = REPOSITORY_ROOT / submission["driver_path"]
    source_sha256 = _sha256(path)
    try:
        audit = audit_driver_source(
            path.read_text(encoding="utf-8"),
            condition=submission["condition"],
            capability_methods=methods,
        )
    except Exception as exc:
        return {
            **submission,
            "status": "current_interface_incompatible",
            "source_sha256": source_sha256,
            "source_unchanged": True,
            "source_audit": {
                "passed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "suite_execution": {
                "executed": False,
                "reason": "candidate cannot cross the current 1.0.4 source/interface boundary",
            },
        }
    return {
        **submission,
        "status": "replay_pending",
        "source_sha256": source_sha256,
        "source_audit": {
            "passed": True,
            "capability_methods": list(audit.capability_methods),
            "imports_trusted_skeleton": audit.imports_trusted_skeleton,
            "ctrl_references": audit.ctrl_references,
            "physics_step_references": audit.physics_step_references,
        },
    }


def _trial_summary(trial: Mapping[str, Any]) -> dict[str, Any]:
    contact = trial.get("contact_integrity")
    exception = trial.get("candidate_exception")
    exception_summary = (
        {
            "type": exception.get("type"),
            "message": exception.get("message"),
        }
        if isinstance(exception, Mapping)
        else None
    )
    return {
        "case_id": trial.get("case_id"),
        "capability_id": trial.get("capability_id"),
        "trial_passed": bool(trial.get("trial_passed")),
        "method_invoked": bool(trial.get("method_invoked")),
        "worker_completed": bool(trial.get("worker_completed")),
        "physical_execution_passed": bool(trial.get("physical_execution_passed")),
        "physical_integrity_passed": bool(trial.get("physical_integrity_passed")),
        "task_metric_passed": bool(trial.get("task_metric_passed")),
        "measurement_value": trial.get("measurement_value"),
        "measurement_error": trial.get("measurement_error"),
        "candidate_exception": exception_summary,
        "guard_outcomes": trial.get("guard_outcomes"),
        "contact_integrity_passed": bool(
            isinstance(contact, Mapping) and contact.get("passed")
        ),
        "minimum_contact_distance_m": (
            contact.get("minimum_contact_distance_m")
            if isinstance(contact, Mapping)
            else None
        ),
    }


def _failure_reasons(report: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not report.get("pipeline_completed"):
        reasons.append("Harness pipeline did not complete cleanly")
    failed_capabilities = [
        str(item.get("capability_id"))
        for item in report.get("capability_results", [])
        if isinstance(item, Mapping) and not item.get("passed")
    ]
    if failed_capabilities:
        reasons.append("failed capabilities: " + ", ".join(failed_capabilities))
    trials = [item for item in report.get("trials", []) if isinstance(item, Mapping)]
    if any(not item.get("method_invoked") for item in trials):
        reasons.append("one or more cases did not invoke the requested capability")
    if any(item.get("candidate_exception") for item in trials):
        reasons.append("one or more cases raised a candidate exception")
    if any(not item.get("physical_execution_passed") for item in trials):
        reasons.append("one or more cases lacked clean actuator-controlled physics execution")
    if any(item.get("measurement_error") for item in trials):
        reasons.append("one or more cases had a trusted measurement error")
    if any(
        isinstance(item.get("contact_integrity"), Mapping)
        and not item["contact_integrity"].get("passed")
        for item in trials
    ):
        reasons.append("one or more cases violated the contact-integrity guard")
    return reasons


def _run_one(payload: dict[str, Any]) -> dict[str, Any]:
    from autoadapter2.harness.runner import run_private_suite
    from autoadapter2.libraries import load_robot_package

    started = time.monotonic()
    candidate = REPOSITORY_ROOT / payload["driver_path"]
    before = _sha256(candidate)
    try:
        package = load_robot_package(PACKAGE_ROOT)
        design = _read_object(DESIGN_PATH)
        suite = _read_object(SUITE_PATH)
        with tempfile.TemporaryDirectory(prefix="so101-legacy-replay-") as output_dir:
            report = run_private_suite(
                package=package,
                design=design,
                suite=suite,
                driver_path=candidate,
                condition=payload["condition"],
                output_dir=output_dir,
                record_video=False,
                wall_timeout_s=120.0,
                run_id=f"diagnostic-legacy-replay::{payload['run_id']}",
                attempt=int(payload["attempt"]),
            )
    except Exception as exc:
        after = _sha256(candidate)
        return {
            **payload,
            "status": "harness_error",
            "source_unchanged": before == after,
            "source_sha256_after": after,
            "elapsed_wall_s": time.monotonic() - started,
            "suite_execution": {
                "executed": True,
                "completed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        }

    after = _sha256(candidate)
    trials = [item for item in report.get("trials", []) if isinstance(item, Mapping)]
    return {
        **payload,
        "status": "replayed",
        "source_unchanged": before == after,
        "source_sha256_after": after,
        "elapsed_wall_s": time.monotonic() - started,
        "suite_execution": {
            "executed": True,
            "completed": bool(report.get("pipeline_completed")),
            "real_mujoco_attempted": True,
            "physical_validation_executed": bool(
                report.get("physical_validation_executed")
            ),
            "physical_execution_case_count": sum(
                bool(item.get("physical_execution_passed")) for item in trials
            ),
            "method_invoked_case_count": sum(
                bool(item.get("method_invoked")) for item in trials
            ),
            "validation_passed": bool(report.get("validation_passed")),
            "passed_private_case_count": int(report.get("passed_private_case_count", 0)),
            "private_case_count": int(report.get("private_case_count", 0)),
            "passed_capability_count": int(report.get("passed_capability_count", 0)),
            "capability_count": int(report.get("capability_count", 0)),
            "capability_results": report.get("capability_results", []),
            "failure_reasons": _failure_reasons(report),
            "cases": [_trial_summary(item) for item in trials],
        },
    }


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def build_inventory() -> dict[str, Any]:
    design = _read_object(DESIGN_PATH)
    suite = _read_object(SUITE_PATH)
    methods = tuple(str(item["method_name"]) for item in design["capabilities"])
    exp1, exp1_inventory = _discover_exp1_submissions()
    demo, demo_inventory = _discover_historical_demo_submissions()
    submissions = exp1 + demo
    audited = [_source_audit(item, methods) for item in submissions]
    located = [item for item in audited if item.get("artifact_located")]
    unique_hashes = {
        item["source_sha256"] for item in located if item.get("source_sha256")
    }
    return {
        "schema_version": "1.0",
        "artifact_type": "so101_legacy_driver_diagnostic_replay",
        "formal": False,
        "denominator_eligible": False,
        "robot_configuration_id": "robotstudio_so101",
        "package_version": "1.0.4",
        "scope": {
            "included": [
                "Exp1 cell_record submit_driver events",
                "historical Demo3 cell_report submit_driver events",
            ],
            "excluded": [
                "reference Drivers",
                "development-only driver.py revisions without submit_driver evidence",
                "staged/probe-runtime copies",
                "Exp2 source and later runs",
            ],
            "note": (
                "Current-interface-incompatible sources are reported but are not adapted "
                "or passed to the 1.0.4 private suite."
            ),
        },
        "inputs": {
            "capability_design_path": _repository_path(DESIGN_PATH),
            "capability_design_sha256": _sha256(DESIGN_PATH),
            "suite_path": _repository_path(SUITE_PATH),
            "suite_sha256": _sha256(SUITE_PATH),
            "suite_id": suite.get("suite_id"),
            "case_count": len(suite.get("cases", [])),
            "capability_methods": list(methods),
            "source_audit_path": _repository_path(SOURCE_AUDIT_PATH),
            "source_audit_sha256": _sha256(SOURCE_AUDIT_PATH),
            "harness_runner_path": _repository_path(HARNESS_RUNNER_PATH),
            "harness_runner_sha256": _sha256(HARNESS_RUNNER_PATH),
        },
        "inventory": {
            "exp1": exp1_inventory,
            "historical_demo3": demo_inventory,
            "included_record_count": exp1_inventory["record_count"]
            + demo_inventory["record_count"],
            "submitted_attempt_count": len(audited),
            "located_driver_count": len(located),
            "missing_driver_count": len(audited) - len(located),
            "unique_driver_source_count": len(unique_hashes),
            "current_interface_compatible_count": sum(
                item["status"] == "replay_pending" for item in audited
            ),
            "current_interface_incompatible_count": sum(
                item["status"] == "current_interface_incompatible" for item in audited
            ),
        },
        "excluded_exp2_inventory": _discover_excluded_exp2(),
        "auxiliary_non_submission_candidates": _discover_auxiliary_route_candidates(),
        "driver_results": audited,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    if arguments.workers < 1 or arguments.workers > 8:
        parser.error("--workers must be between 1 and 8")

    document = build_inventory()
    pending = [
        item for item in document["driver_results"] if item["status"] == "replay_pending"
    ]
    if not arguments.inventory_only and pending:
        completed: dict[tuple[str, Any], dict[str, Any]] = {}
        # Harness cases execute in isolated subprocess workers.  A thread pool
        # is sufficient to overlap those workers and avoids relying on host
        # process-semaphore limits in restricted experiment environments.
        with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
            futures = {executor.submit(_run_one, item): item for item in pending}
            for future in as_completed(futures):
                item = future.result()
                completed[(str(item["run_id"]), item["attempt"])] = item
        document["driver_results"] = [
            completed.get((str(item["run_id"]), item["attempt"]), item)
            for item in document["driver_results"]
        ]

    results = document["driver_results"]
    replayed = [item for item in results if item["status"] == "replayed"]
    suite_results = [item["suite_execution"] for item in replayed]
    incompatibility_counts = Counter(
        str(item.get("source_audit", {}).get("error"))
        for item in results
        if item["status"] == "current_interface_incompatible"
    )
    document["summary"] = {
        "inventory_only": bool(arguments.inventory_only),
        "replay_executed_count": len(replayed),
        "real_mujoco_attempt_count": sum(
            bool(item.get("suite_execution", {}).get("real_mujoco_attempted"))
            for item in replayed
        ),
        "whole_suite_pass_count": sum(
            bool(item.get("suite_execution", {}).get("validation_passed"))
            for item in replayed
        ),
        "physical_validation_completed_driver_count": sum(
            bool(item.get("physical_validation_executed")) for item in suite_results
        ),
        "physical_execution_case_count": sum(
            int(item.get("physical_execution_case_count", 0)) for item in suite_results
        ),
        "passed_private_case_count": sum(
            int(item.get("passed_private_case_count", 0)) for item in suite_results
        ),
        "private_case_count": sum(
            int(item.get("private_case_count", 0)) for item in suite_results
        ),
        "passed_capability_count": sum(
            int(item.get("passed_capability_count", 0)) for item in suite_results
        ),
        "capability_count": sum(
            int(item.get("capability_count", 0)) for item in suite_results
        ),
        "harness_error_count": sum(item["status"] == "harness_error" for item in results),
        "all_located_sources_unchanged": all(
            item.get("source_unchanged", True)
            for item in results
            if item.get("artifact_located")
        ),
        "status_counts": dict(sorted(Counter(item["status"] for item in results).items())),
        "current_interface_incompatibility_counts": dict(
            sorted(incompatibility_counts.items())
        ),
    }
    document["execution"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "framework_git_commit": _git_commit(),
        "python": os.sys.version.split()[0],
        "record_video": False,
        "workers": 0 if arguments.inventory_only else arguments.workers,
    }
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "inventory": document["inventory"],
                "summary": document["summary"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
