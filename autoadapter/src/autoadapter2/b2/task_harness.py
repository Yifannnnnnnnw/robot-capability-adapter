"""Trusted terminal task verdict for one persistent B2 worker session.

The controller and its self-reported status are deliberately absent from the
verdict path.  This module consumes the worker's complete physical evidence
and evaluates the selected package task with the same private bindings,
guards, temporal reducers, and aggregators used by the main Harness.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    instance_id: str,
    session_result: Mapping[str, Any],
    repetition_index: int = 0,
) -> dict[str, Any]:
    """Issue the physical task verdict for one B2 controller episode.

    ``session_result`` is the object returned by
    :func:`autoadapter2.b2.session_runner.run_recap_worker_session`.  The task
    criterion and public measurement arguments are loaded from the original
    package task and private instance, rather than from controller output.

    One invocation evaluates one physical episode.  ``repetition_index``
    selects the matching private repetition variant when the source protocol
    defines more than one independently reset trial.
    """

    if not isinstance(instance_id, str) or not instance_id.strip():
        raise HarnessError("B2 instance_id must be a non-empty string")
    if (
        not isinstance(repetition_index, int)
        or isinstance(repetition_index, bool)
        or repetition_index < 0
    ):
        raise HarnessError("B2 repetition_index must be a non-negative integer")
    if not isinstance(session_result, Mapping):
        raise HarnessError("B2 session result must be an object")

    worker = session_result.get("worker")
    if not isinstance(worker, Mapping):
        raise HarnessError("B2 session result has no worker evidence")

    instances = _indexed(
        _read_object(package.private_dir / "instances.json"),
        "instances",
        "instance_id",
    )
    bindings = _indexed(
        _read_object(package.private_dir / "bindings.json"),
        "bindings",
        "binding_id",
    )
    guards = _indexed(
        _read_object(package.private_dir / "guards.json"),
        "guards",
        "guard_id",
    )
    try:
        instance = instances[instance_id]
    except KeyError as exc:
        raise HarnessError(f"unknown B2 private instance {instance_id!r}") from exc

    task_id = instance.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise HarnessError("B2 private instance has no task_id")
    matching_tasks = [task for task in package.tasks if task.get("task_id") == task_id]
    if len(matching_tasks) != 1:
        raise HarnessError(
            f"B2 package must contain exactly one task definition for {task_id!r}"
        )
    task = matching_tasks[0]

    repetitions = instance.get("repetitions", 1)
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or repetitions <= 0
        or repetition_index >= repetitions
    ):
        raise HarnessError("B2 repetition_index is outside the private instance")
    variants = instance.get("repetition_variants")
    if variants is None:
        variant: Mapping[str, Any] = {}
    elif (
        not isinstance(variants, list)
        or len(variants) != repetitions
        or not all(isinstance(item, Mapping) for item in variants)
    ):
        raise HarnessError(
            "B2 private repetition variants must cover every repetition"
        )
    else:
        variant = variants[repetition_index]

    public_arguments = variant.get(
        "public_arguments", instance.get("public_arguments", {})
    )
    if not isinstance(public_arguments, Mapping):
        raise HarnessError("B2 private public_arguments must be an object")

    clause_bindings = instance.get("clause_bindings")
    if not isinstance(clause_bindings, Mapping):
        raise HarnessError("B2 private instance has no clause_bindings")
    scoring = task.get("scoring")
    if not isinstance(scoring, list) or not scoring:
        raise HarnessError(f"B2 task {task_id!r} has no scoring clauses")

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

    guard_ids = instance.get("guard_ids")
    if (
        not isinstance(guard_ids, list)
        or not guard_ids
        or not all(isinstance(guard_id, str) for guard_id in guard_ids)
    ):
        raise HarnessError("B2 private instance has no valid guard_ids")
    try:
        guard_definitions = [guards[guard_id] for guard_id in guard_ids]
    except KeyError as exc:
        raise HarnessError(f"B2 private instance references unknown guard {exc.args[0]!r}") from exc

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
        "repetition_index": repetition_index,
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


__all__ = ["evaluate_b2_task_harness"]
