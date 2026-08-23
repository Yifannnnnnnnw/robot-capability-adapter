"""Trusted terminal task verdict for one persistent B2 worker session.

The controller and its self-reported status are deliberately absent from the
verdict path.  This module consumes the worker's complete physical evidence
and evaluates the selected package task with the same private bindings,
guards, temporal reducers, and aggregators used by the main Harness.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoadapter2.harness.measurements import (
    aggregate_criterion,
    evaluate_guards,
    evaluate_temporal,
)
from autoadapter2.harness.runner import (
    HarnessError,
    _contact_integrity,
    _indexed,
    _physical_execution_completed,
    _read_object,
)
from autoadapter2.libraries import RobotPackage


def evaluate_b2_task_harness(
    *,
    package: RobotPackage,
    task_suite_path: str | Path,
    instance_id: str,
    replicate_id: str,
    session_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Issue the physical task verdict for one B2 controller episode.

    ``session_result`` is the object returned by
    :func:`autoadapter2.b2.session_runner.run_recap_worker_session`.  The task
    criterion and public measurement arguments are loaded from the sealed B2
    task-suite snapshot, rather than from controller output or mutable package
    task records.  The caller supplies the versioned snapshot path so the
    canonical mainline module does not resolve an experiment sibling itself.

    One invocation evaluates one fresh, single-reset physical episode for the
    selected B2 replicate.
    """

    if not isinstance(instance_id, str) or not instance_id.strip():
        raise HarnessError("B2 instance_id must be a non-empty string")
    if not isinstance(replicate_id, str) or not replicate_id.strip():
        raise HarnessError("B2 replicate_id must be a non-empty string")
    if not isinstance(session_result, Mapping):
        raise HarnessError("B2 session result must be an object")

    worker = session_result.get("worker")
    if not isinstance(worker, Mapping):
        raise HarnessError("B2 session result has no worker evidence")

    sealed = _sealed_task_definition(
        package=package,
        task_suite_path=task_suite_path,
        instance_id=instance_id,
        replicate_id=replicate_id,
    )
    task_id = sealed["task_id"]
    scoring = sealed["scoring"]
    clause_bindings = sealed["clause_bindings"]
    bindings = sealed["bindings"]
    guard_definitions = sealed["guards"]
    public_arguments = sealed["public_arguments"]

    evidence = worker.get("physical_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    clause_results: list[dict[str, Any]] = []
    for clause in scoring:
        if not isinstance(clause, Mapping):
            raise HarnessError(f"B2 task {task_id!r} has an invalid scoring clause")
        clause_id = clause.get("clause_id")
        if not isinstance(clause_id, str) or not clause_id:
            raise HarnessError(f"B2 task {task_id!r} has an invalid clause_id")
        binding_id = clause_bindings.get(clause_id)
        if not isinstance(binding_id, str) or binding_id not in bindings:
            raise HarnessError(
                f"B2 instance {instance_id!r} has no binding for clause {clause_id!r}"
            )

        temporal_evidence: dict[str, Any] = {}
        aggregation_evidence: dict[str, Any] = {
            "kind": None,
            "passed": False,
            "value": None,
        }
        measurement_error: str | None = None
        measurement_value: Any = None
        temporal_passed = False
        try:
            temporal_evidence = evaluate_temporal(
                bindings[binding_id],
                criterion=clause,
                evidence=evidence,
                public_arguments=public_arguments,
            )
            measurement_value = temporal_evidence.get("value")
            temporal_passed = bool(temporal_evidence.get("passed"))
            aggregation = clause.get("aggregation")
            if not isinstance(aggregation, Mapping):
                raise ValueError("criterion aggregation rule must be an object")
            aggregation_evidence = aggregate_criterion(
                aggregation,
                comparator=str(clause["comparator"]),
                threshold=clause["threshold"],
                values=[measurement_value],
                temporal_passes=[temporal_passed],
            )
        except Exception as exc:
            measurement_error = f"{type(exc).__name__}: {exc}"

        clause_results.append(
            {
                "clause_id": clause_id,
                "binding_id": binding_id,
                "measurement_value": measurement_value,
                "measurement_error": measurement_error,
                "temporal_passed": temporal_passed,
                "temporal_evidence": temporal_evidence,
                "aggregation_passed": bool(aggregation_evidence.get("passed")),
                "aggregation_evidence": aggregation_evidence,
                "task_metric_passed": (
                    measurement_error is None
                    and temporal_passed
                    and bool(aggregation_evidence.get("passed"))
                ),
            }
        )

    guard_error: str | None = None
    try:
        guard_outcomes = evaluate_guards(guard_definitions, worker_result=worker)
    except Exception as exc:
        guard_outcomes = {}
        guard_error = f"{type(exc).__name__}: {exc}"

    physical_execution_passed = _physical_execution_completed(worker)
    contact_integrity = _contact_integrity(worker)
    task_metric_passed = bool(clause_results) and all(
        bool(result["task_metric_passed"]) for result in clause_results
    )
    physical_integrity_passed = (
        physical_execution_passed
        and bool(contact_integrity.get("passed"))
        and guard_error is None
        and bool(guard_outcomes)
        and all(guard_outcomes.values())
    )

    video = worker.get("video")
    video_manifest = dict(video) if isinstance(video, Mapping) else {}
    video_complete = (
        video_manifest.get("requested") is True
        and video_manifest.get("complete") is True
    )
    physical_harness_passed = (
        task_metric_passed and physical_integrity_passed and video_complete
    )

    return {
        "robot_configuration_id": package.robot_configuration_id,
        "task_id": task_id,
        "instance_id": instance_id,
        "replicate_id": replicate_id,
        "task_snapshot_id": sealed["task_snapshot_id"],
        "scene_entrypoint": sealed["scene_entrypoint"],
        "reset_seed": sealed["reset_seed"],
        "reset_seed_applied": sealed["reset_seed_applied"],
        "task_clause_results": clause_results,
        "task_metric_passed": task_metric_passed,
        "physical_execution_passed": physical_execution_passed,
        "guard_outcomes": guard_outcomes,
        "guard_error": guard_error,
        "contact_integrity": contact_integrity,
        "physical_integrity_passed": physical_integrity_passed,
        "video_complete": video_complete,
        "video": video_manifest,
        "physical_harness_verdict": "PASS" if physical_harness_passed else "FAIL",
    }


def _sealed_task_definition(
    *,
    package: RobotPackage,
    task_suite_path: str | Path,
    instance_id: str,
    replicate_id: str,
) -> dict[str, Any]:
    suite = _read_object(Path(task_suite_path).resolve())
    authority = suite.get("authority")
    if (
        suite.get("artifact_type") != "b2_recap_task_suite"
        or suite.get("schema_version") != "1.0"
        or not isinstance(authority, Mapping)
        or authority.get("document_id") != "AA2-B2"
        or authority.get("revision") != "0.1.4"
    ):
        raise HarnessError("B2 task suite has an incompatible identity")

    robot_suites = suite.get("robot_suites")
    if not isinstance(robot_suites, list):
        raise HarnessError("B2 task suite has no robot_suites")
    matching_robots = [
        robot
        for robot in robot_suites
        if isinstance(robot, Mapping)
        and robot.get("robot_configuration_id") == package.robot_configuration_id
    ]
    if len(matching_robots) != 1:
        raise HarnessError(
            f"B2 task suite must contain robot {package.robot_configuration_id!r} exactly once"
        )
    robot = matching_robots[0]
    if (
        robot.get("package_version") != package.package_version
        or robot.get("task_snapshot_id") != package.snapshot_id
    ):
        raise HarnessError("B2 task suite does not match the selected package identity")

    tasks = robot.get("tasks")
    if not isinstance(tasks, list):
        raise HarnessError("B2 robot suite has no tasks")
    matching_tasks = [
        task
        for task in tasks
        if isinstance(task, Mapping)
        and task.get("private_instance_id") == instance_id
    ]
    if len(matching_tasks) != 1:
        raise HarnessError(
            f"unknown or duplicate sealed B2 private instance {instance_id!r}"
        )
    task = matching_tasks[0]
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise HarnessError("sealed B2 task has no task_id")

    replicate_inputs = task.get("replicate_inputs")
    if not isinstance(replicate_inputs, list):
        raise HarnessError(f"sealed B2 task {task_id!r} has no replicate inputs")
    matching_replicates = [
        replicate
        for replicate in replicate_inputs
        if isinstance(replicate, Mapping)
        and replicate.get("replicate_id") == replicate_id
    ]
    if len(matching_replicates) != 1:
        raise HarnessError(
            f"sealed B2 task {task_id!r} has no unique replicate {replicate_id!r}"
        )
    replicate = matching_replicates[0]

    public_projection = task.get("public_projection")
    scoring = task.get("private_scoring_clauses")
    clause_bindings = task.get("private_clause_bindings")
    raw_bindings = task.get("private_measurement_bindings")
    raw_guards = task.get("private_guards")
    if (
        not isinstance(public_projection, Mapping)
        or public_projection.get("task_id") != task_id
        or not isinstance(public_projection.get("request"), Mapping)
        or not isinstance(scoring, list)
        or not scoring
        or not isinstance(clause_bindings, Mapping)
        or not isinstance(raw_bindings, list)
        or not isinstance(raw_guards, list)
        or not raw_guards
    ):
        raise HarnessError(f"sealed B2 task {task_id!r} is incomplete")
    bindings = _indexed(
        {"bindings": raw_bindings},
        "bindings",
        "binding_id",
    )
    guard_index = _indexed(
        {"guards": raw_guards},
        "guards",
        "guard_id",
    )
    guard_definitions = list(guard_index.values())

    scene_entrypoint = replicate.get("scene_entrypoint")
    reset_seed_applied = replicate.get("reset_seed_applied")
    if (
        not isinstance(scene_entrypoint, str)
        or not scene_entrypoint
        or replicate.get("reset_seed") is not None
        or reset_seed_applied is not False
    ):
        raise HarnessError(f"sealed B2 replicate {replicate_id!r} is incomplete")

    return {
        "task_id": task_id,
        "task_snapshot_id": robot["task_snapshot_id"],
        "scene_entrypoint": scene_entrypoint,
        "reset_seed": None,
        "reset_seed_applied": False,
        "public_arguments": {"request": dict(public_projection["request"])},
        "scoring": scoring,
        "clause_bindings": clause_bindings,
        "bindings": bindings,
        "guards": guard_definitions,
    }


__all__ = ["evaluate_b2_task_harness"]
