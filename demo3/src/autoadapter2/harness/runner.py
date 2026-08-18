"""Parent-side private-suite execution, measurement, and verdict aggregation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.libraries import RobotPackage

from .measurements import (
    aggregate_criterion,
    evaluate_guards,
    evaluate_temporal,
)


class HarnessError(RuntimeError):
    """Raised when trusted validation infrastructure cannot execute a case."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read Harness input {path}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"Harness input {path} must be an object")
    return value


def _indexed(document: Mapping[str, Any], field: str, id_field: str) -> dict[str, Mapping[str, Any]]:
    values = document.get(field)
    if not isinstance(values, list):
        raise HarnessError(f"private {field} must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping) or not isinstance(value.get(id_field), str):
            raise HarnessError(f"private {field} contains an invalid item")
        result[str(value[id_field])] = value
    return result


def _worker_environment(source_root: Path) -> dict[str, str]:
    allowed = {}
    for name in ("PATH", "TMPDIR", "MUJOCO_GL", "DYLD_LIBRARY_PATH"):
        value = os.environ.get(name)
        if value:
            allowed[name] = value
    allowed["PYTHONPATH"] = str(source_root)
    allowed["PYTHONNOUSERSITE"] = "1"
    allowed["PYTHONDONTWRITEBYTECODE"] = "1"
    return allowed


def _run_worker(
    payload: Mapping[str, Any],
    *,
    candidate: Path,
    source_root: Path,
    wall_timeout_s: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="autoadapter2-eval-") as temporary:
        evaluation_workspace = Path(temporary)
        staged_driver = evaluation_workspace / "driver.py"
        shutil.copyfile(candidate, staged_driver)
        worker_payload = dict(payload)
        worker_payload["driver_path"] = str(staged_driver)
        completed = subprocess.run(
            [sys.executable, "-m", "autoadapter2.harness.worker"],
            input=json.dumps(worker_payload, ensure_ascii=True),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=evaluation_workspace,
            env=_worker_environment(source_root),
            timeout=wall_timeout_s,
            check=False,
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"candidate worker returned invalid JSON; stderr={completed.stderr[-1000:]}"
        ) from exc
    if not isinstance(result, dict):
        raise HarnessError("candidate worker result must be an object")
    return result


def _physical_execution_completed(worker: Mapping[str, Any]) -> bool:
    evidence = worker.get("physical_evidence")
    if not isinstance(evidence, Mapping):
        return False
    step_count = evidence.get("step_count")
    return (
        worker.get("worker_completed") is True
        and worker.get("method_invoked") is True
        and worker.get("candidate_exception") is None
        and worker.get("canonical_model_data") is True
        and isinstance(step_count, (int, float))
        and not isinstance(step_count, bool)
        and float(step_count) > 0
    )


def _designed_task_clauses(design: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise HarnessError("Capability Design has no capabilities")
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            raise HarnessError("Capability Design contains an invalid capability")
        clauses = capability.get("validation_contract")
        if not isinstance(clauses, list):
            raise HarnessError("Capability Design capability has no validation contract")
        for clause in clauses:
            if not isinstance(clause, Mapping):
                raise HarnessError("Capability Design contains an invalid validation clause")
            task_id = clause.get("source_task_id")
            clause_id = clause.get("source_clause_id")
            if not isinstance(task_id, str) or not isinstance(clause_id, str):
                raise HarnessError("Capability Design validation clause has invalid source IDs")
            result.setdefault(task_id, set()).add(clause_id)
    return result


def run_private_suite(
    *,
    package: RobotPackage,
    design: Mapping[str, Any],
    suite: Mapping[str, Any],
    driver_path: str | Path,
    condition: str,
    output_dir: str | Path,
    record_video: bool = True,
    wall_timeout_s: float = 120.0,
    run_id: str = "unassigned",
    attempt: int = 0,
) -> dict[str, Any]:
    """Run every repetition independently and issue the authoritative verdict."""

    candidate = Path(driver_path).resolve()
    capability_methods = tuple(
        str(capability["method_name"]) for capability in design["capabilities"]
    )
    source_audit = audit_driver_source(
        candidate.read_text(encoding="utf-8"),
        condition=condition,  # type: ignore[arg-type]
        capability_methods=capability_methods,
    )
    private_instances = _indexed(
        _read_object(package.private_dir / "instances.json"), "instances", "instance_id"
    )
    private_bindings = _indexed(
        _read_object(package.private_dir / "bindings.json"), "bindings", "binding_id"
    )
    private_guards = _indexed(
        _read_object(package.private_dir / "guards.json"), "guards", "guard_id"
    )
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise HarnessError("private suite has no cases")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parents[2]
    trials: list[dict[str, Any]] = []
    pipeline_completed = True

    for case in cases:
        if not isinstance(case, Mapping):
            raise HarnessError("private suite contains an invalid case")
        instance = private_instances[str(case["instance_id"])]
        binding = private_bindings[str(case["binding_id"])]
        guards = [private_guards[str(guard_id)] for guard_id in case["guard_ids"]]
        repetitions = int(case["repetitions"])
        repetition_variants = instance.get("repetition_variants")
        if repetition_variants is not None and (
            not isinstance(repetition_variants, list)
            or len(repetition_variants) != repetitions
        ):
            raise HarnessError(
                "private repetition variants must contain one entry per repetition"
            )
        scene_relative = str(instance.get("scene_entrypoint", package.morphology["mjcf_entrypoint"]))
        scene_path = (package.root / scene_relative).resolve()
        try:
            scene_path.relative_to((package.root / "assets").resolve())
        except ValueError as exc:
            raise HarnessError("private instance scene escapes package assets") from exc
        for repetition in range(repetitions):
            variant = (
                repetition_variants[repetition]
                if isinstance(repetition_variants, list)
                else {}
            )
            if not isinstance(variant, Mapping):
                raise HarnessError("private repetition variant must be an object")
            public_arguments = variant.get(
                "public_arguments", instance.get("public_arguments", {})
            )
            reset = variant.get("reset", instance.get("reset", {"kind": "default"}))
            trial_id = f"{case['case_id']}-r{repetition:02d}"
            video_path = destination / "videos" / f"{trial_id}.mp4"
            video_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "scene_path": str(scene_path),
                "capability_methods": capability_methods,
                "method_name": case["method_name"],
                "public_arguments": public_arguments,
                "reset": reset,
                "max_steps": int(instance.get("max_steps", 10000)),
                "max_sim_time_s": float(case["timeout_sim_s"]),
                "sample_hz": float(
                    instance.get("video_fps", 10.0)
                    if record_video
                    else instance.get("sample_hz", 20.0)
                ),
                "render": {
                    "enabled": record_video,
                    "width": int(instance.get("video_width", 800)),
                    "height": int(instance.get("video_height", 600)),
                    "fps": float(instance.get("video_fps", 10.0)),
                    "camera": instance.get("camera", -1),
                },
                "video_path": str(video_path),
            }
            try:
                worker = _run_worker(
                    payload,
                    candidate=candidate,
                    source_root=source_root,
                    wall_timeout_s=wall_timeout_s,
                )
            except (subprocess.TimeoutExpired, HarnessError) as exc:
                pipeline_completed = False
                worker = {
                    "worker_completed": False,
                    "worker_error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                    "physical_evidence": {},
                    "video": {"requested": record_video, "complete": False},
                }
            if not worker.get("worker_completed"):
                pipeline_completed = False

            measurement_value = None
            temporal_passed = False
            temporal_evidence: dict[str, Any] = {}
            guard_outcomes: dict[str, bool] = {}
            measurement_error = None
            criterion = case.get("criterion")
            try:
                guard_outcomes = evaluate_guards(guards, worker_result=worker)
                if worker.get("candidate_exception") is None and worker.get("method_invoked"):
                    if not isinstance(criterion, Mapping):
                        raise ValueError("private case criterion must be an object")
                    temporal_evidence = evaluate_temporal(
                        binding,
                        criterion=criterion,
                        evidence=worker["physical_evidence"],
                        public_arguments=public_arguments,
                    )
                    measurement_value = temporal_evidence.get("value")
                    temporal_passed = bool(temporal_evidence.get("passed"))
            except Exception as exc:
                measurement_error = f"{type(exc).__name__}: {exc}"
            physical_execution_passed = _physical_execution_completed(worker)
            video = worker.get("video", {})
            video_manifest = dict(video) if isinstance(video, Mapping) else {}
            if record_video and not physical_execution_passed:
                video_manifest["complete"] = False
                video_manifest["error"] = video_manifest.get("error") or (
                    "recording ended before clean actuator-controlled physics execution "
                    "completed"
                )
            video_evidence_passed = (not record_video) or bool(
                video_manifest.get("complete")
            )
            base_passed = (
                physical_execution_passed
                and bool(guard_outcomes)
                and all(guard_outcomes.values())
                and video_evidence_passed
            )
            physical_evidence = worker.get("physical_evidence")
            samples = (
                physical_evidence.get("samples", [])
                if isinstance(physical_evidence, Mapping)
                else []
            )
            sim_start_s = samples[0].get("time") if samples else None
            sim_end_s = samples[-1].get("time") if samples else None
            video_manifest.update(
                {
                    "robot_configuration_id": package.robot_configuration_id,
                    "run_id": run_id,
                    "attempt": attempt,
                    "trial_id": trial_id,
                    "case_id": case["case_id"],
                    "sim_start_s": sim_start_s,
                    "sim_end_s": sim_end_s,
                }
            )
            trials.append(
                {
                    "trial_id": trial_id,
                    "case_id": case["case_id"],
                    "capability_id": case["capability_id"],
                    "task_id": case["task_id"],
                    "source_clause_id": case["source_clause_id"],
                    "worker_completed": bool(worker.get("worker_completed")),
                    "method_invoked": bool(worker.get("method_invoked")),
                    "public_arguments": public_arguments,
                    "measurement_value": measurement_value,
                    "measurement_error": measurement_error,
                    "criterion_passed": False,
                    "temporal_passed": temporal_passed,
                    "temporal_evidence": temporal_evidence,
                    "aggregation_passed": False,
                    "aggregation_value": None,
                    "guard_outcomes": guard_outcomes,
                    "video": video_manifest,
                    "candidate_exception": worker.get("candidate_exception"),
                    "candidate_log": worker.get("candidate_log", ""),
                    "physical_evidence": worker.get("physical_evidence", {}),
                    "physical_execution_passed": physical_execution_passed,
                    "trial_passed": False,
                    "_criterion": criterion,
                    "_base_passed": base_passed,
                }
            )

    case_trials: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        case_trials.setdefault(str(trial["case_id"]), []).append(trial)
    for values in case_trials.values():
        criterion = values[0].get("_criterion")
        aggregation_result: dict[str, Any]
        try:
            if not isinstance(criterion, Mapping):
                raise ValueError("private case criterion must be an object")
            aggregation = criterion.get("aggregation")
            if not isinstance(aggregation, Mapping):
                raise ValueError("criterion aggregation rule must be an object")
            aggregation_result = aggregate_criterion(
                aggregation,
                comparator=str(criterion["comparator"]),
                threshold=criterion["threshold"],
                values=[trial["measurement_value"] for trial in values],
                temporal_passes=[bool(trial["temporal_passed"]) for trial in values],
            )
        except Exception as exc:
            aggregation_result = {
                "kind": (
                    criterion.get("aggregation", {}).get("kind")
                    if isinstance(criterion, Mapping)
                    and isinstance(criterion.get("aggregation"), Mapping)
                    else None
                ),
                "passed": False,
                "value": None,
            }
            error = f"{type(exc).__name__}: {exc}"
            for trial in values:
                trial["aggregation_error"] = error
        for trial in values:
            trial["aggregation_passed"] = bool(aggregation_result["passed"])
            trial["aggregation_value"] = aggregation_result.get("value")
            trial["criterion_passed"] = bool(aggregation_result["passed"])
            trial["trial_passed"] = (
                bool(trial["_base_passed"])
                and bool(trial["temporal_passed"])
                and bool(aggregation_result["passed"])
            )
            trial.pop("_criterion", None)
            trial.pop("_base_passed", None)

    task_passes: Counter[str] = Counter()
    task_totals: Counter[str] = Counter()
    for trial in trials:
        task_totals[str(trial["task_id"])] += 1
        if trial["trial_passed"]:
            task_passes[str(trial["task_id"])] += 1
    passed_selected_tasks = sum(
        1 for task_id, total in task_totals.items() if task_passes[task_id] == total
    )
    clause_trials: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trial in trials:
        key = (str(trial["task_id"]), str(trial["source_clause_id"]))
        clause_trials.setdefault(key, []).append(trial)
    passed_cases = sum(
        1 for values in case_trials.values() if all(value["trial_passed"] for value in values)
    )
    passed_clauses = sum(
        1 for values in clause_trials.values() if all(value["trial_passed"] for value in values)
    )
    designed_task_clauses = _designed_task_clauses(design)
    selected_task_clauses: dict[str, set[str]] = {}
    for task_id, clause_id in clause_trials:
        selected_task_clauses.setdefault(task_id, set()).add(clause_id)
    fully_evaluated_tasks = {
        task_id
        for task_id, selected_clauses in selected_task_clauses.items()
        if designed_task_clauses.get(task_id) == selected_clauses
    }
    passed_fully_evaluated_tasks = sum(
        1
        for task_id in fully_evaluated_tasks
        if task_passes[task_id] == task_totals[task_id]
    )
    physical_executed = bool(trials) and all(
        trial["physical_execution_passed"] for trial in trials
    )
    validation_passed = bool(trials) and all(trial["trial_passed"] for trial in trials)
    video_complete = bool(trials) and all(
        (not record_video) or bool(trial["video"].get("complete")) for trial in trials
    )
    return {
        "pipeline_completed": pipeline_completed,
        "physical_validation_executed": physical_executed,
        "validation_passed": validation_passed,
        "passed_task_count": passed_fully_evaluated_tasks,
        "task_count": len(fully_evaluated_tasks),
        "selected_task_count": len(task_totals),
        "passed_selected_task_count": passed_selected_tasks,
        "fully_evaluated_task_count": len(fully_evaluated_tasks),
        "passed_fully_evaluated_task_count": passed_fully_evaluated_tasks,
        "passed_source_clause_count": passed_clauses,
        "source_clause_count": len(clause_trials),
        "passed_private_case_count": passed_cases,
        "private_case_count": len(case_trials),
        "video_complete": video_complete,
        "video_manifest": [trial["video"] for trial in trials],
        "run_id": run_id,
        "attempt": attempt,
        "condition": condition,
        "source_audit": {
            "capability_methods": list(source_audit.capability_methods),
            "imports_trusted_skeleton": source_audit.imports_trusted_skeleton,
        },
        "trials": trials,
    }
