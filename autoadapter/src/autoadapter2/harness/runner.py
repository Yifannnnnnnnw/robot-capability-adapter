"""Parent-side private-suite execution, measurement, and verdict aggregation."""

from __future__ import annotations

import json
import math
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolModelClient
from autoadapter2.task_demo import run_task_demo_react

from .measurements import (
    aggregate_criterion,
    evaluate_guards,
    evaluate_temporal,
)


class HarnessError(RuntimeError):
    """Raised when trusted validation infrastructure cannot execute a case."""


_MAXIMUM_ALLOWED_PENETRATION_M = 0.005


def _contact_integrity(worker_result: Mapping[str, Any]) -> dict[str, Any]:
    """Reject geometric overlap beyond the shared soft-contact tolerance."""

    evidence = worker_result.get("physical_evidence")
    result: dict[str, Any] = {
        "passed": False,
        "maximum_allowed_penetration_m": _MAXIMUM_ALLOWED_PENETRATION_M,
        "minimum_contact_distance_m": None,
        "deepest_contact_pair": None,
    }
    if not isinstance(evidence, Mapping) or not bool(
        evidence.get("contact_monitoring_complete")
    ):
        return result

    value = evidence.get("minimum_contact_distance_m")
    if value is None:
        result["passed"] = True
        return result
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return result
    minimum_distance = float(value)
    if not math.isfinite(minimum_distance):
        return result

    result["minimum_contact_distance_m"] = minimum_distance
    records = evidence.get("contact_pair_min_distances")
    if isinstance(records, list):
        deepest: Mapping[str, Any] | None = None
        for record in records:
            if not isinstance(record, Mapping):
                continue
            distance = record.get("minimum_distance_m")
            if (
                isinstance(distance, (int, float))
                and not isinstance(distance, bool)
                and math.isfinite(float(distance))
                and (
                    deepest is None
                    or float(distance)
                    < float(deepest["minimum_distance_m"])
                )
            ):
                deepest = record
        if deepest is not None:
            result["deepest_contact_pair"] = {
                "geom1": deepest.get("geom1"),
                "geom2": deepest.get("geom2"),
                "minimum_distance_m": float(deepest["minimum_distance_m"]),
            }
    result["passed"] = minimum_distance >= -_MAXIMUM_ALLOWED_PENETRATION_M
    return result


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


def _write_protocol(process: subprocess.Popen[str], value: Mapping[str, Any]) -> None:
    if process.stdin is None:
        raise HarnessError("ReAct candidate worker has no command stream")
    try:
        process.stdin.write(json.dumps(dict(value), ensure_ascii=True) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise HarnessError("ReAct candidate worker command stream closed") from exc


def _read_protocol(
    process: subprocess.Popen[str], *, wall_timeout_s: float
) -> dict[str, Any]:
    if process.stdout is None:
        raise HarnessError("ReAct candidate worker has no protocol stream")
    readable, _, _ = select.select([process.stdout], [], [], wall_timeout_s)
    if not readable:
        raise HarnessError(
            f"ReAct candidate worker did not respond within {wall_timeout_s} seconds"
        )
    line = process.stdout.readline()
    if not line:
        raise HarnessError("ReAct candidate worker protocol ended unexpectedly")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise HarnessError("ReAct candidate worker returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise HarnessError("ReAct candidate worker response must be an object")
    return value


def _controller_failure(error: BaseException) -> dict[str, Any]:
    trace = getattr(error, "trace", ())
    public_trace = [
        dict(item)
        for item in trace
        if isinstance(item, Mapping)
    ]
    return {
        "kind": "fixed_react",
        "status": "ERROR",
        "completed": False,
        "model_turns": int(getattr(error, "model_turns", 0)),
        "tool_calls": int(getattr(error, "tool_calls", 0)),
        "capability_calls": sum(
            1
            for item in public_trace
            if item.get("tool") not in {None, "finish_task_demo"}
        ),
        "trace": public_trace,
        "error": {
            "type": type(error).__name__,
            "message": str(error)[:1000],
        },
    }


def _run_react_worker(
    payload: Mapping[str, Any],
    *,
    candidate: Path,
    source_root: Path,
    wall_timeout_s: float,
    controller_client: ToolModelClient,
    package: RobotPackage,
    design: Mapping[str, Any],
    task_id: str,
    public_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Run parent-side ReAct against one credential-free persistent worker."""

    with tempfile.TemporaryDirectory(prefix="autoadapter2-react-eval-") as temporary:
        evaluation_workspace = Path(temporary)
        staged_driver = evaluation_workspace / "driver.py"
        shutil.copyfile(candidate, staged_driver)
        worker_payload = dict(payload)
        worker_payload["driver_path"] = str(staged_driver)
        process = subprocess.Popen(
            [sys.executable, "-m", "autoadapter2.harness.react_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=evaluation_workspace,
            env=_worker_environment(source_root),
        )
        worker_result: dict[str, Any] | None = None
        controller_report: dict[str, Any]
        remaining_worker_s = float(wall_timeout_s)

        def read_worker_response() -> dict[str, Any]:
            nonlocal remaining_worker_s
            if remaining_worker_s <= 0.0:
                raise HarnessError("ReAct candidate worker exhausted its wall-time budget")
            started = time.monotonic()
            try:
                return _read_protocol(
                    process, wall_timeout_s=remaining_worker_s
                )
            finally:
                remaining_worker_s -= time.monotonic() - started

        try:
            _write_protocol(process, worker_payload)
            first = read_worker_response()
            if first.get("type") == "final":
                raw_result = first.get("result")
                if not isinstance(raw_result, Mapping):
                    raise HarnessError("ReAct candidate worker final result is invalid")
                worker_result = dict(raw_result)
                controller_report = {
                    "kind": "fixed_react",
                    "status": "NOT_STARTED",
                    "completed": False,
                    "model_turns": 0,
                    "tool_calls": 0,
                    "capability_calls": 0,
                    "trace": [],
                }
            elif first.get("type") != "ready":
                raise HarnessError("ReAct candidate worker did not become ready")
            else:
                early_final: dict[str, Any] | None = None
                worker_aborted = False

                def invoke(
                    method_name: str, arguments: Mapping[str, Any]
                ) -> Mapping[str, Any]:
                    nonlocal early_final, worker_aborted
                    if early_final is not None:
                        return {"status": "ERROR", "error": "worker already stopped"}
                    if worker_aborted:
                        return {"status": "ABORT"}
                    try:
                        _write_protocol(
                            process,
                            {
                                "type": "invoke",
                                "method_name": method_name,
                                "arguments": dict(arguments),
                            },
                        )
                        response = read_worker_response()
                    except HarnessError:
                        worker_aborted = True
                        if process.poll() is None:
                            process.kill()
                        return {"status": "ABORT"}
                    if response.get("type") == "final":
                        raw_final = response.get("result")
                        if isinstance(raw_final, Mapping):
                            early_final = dict(raw_final)
                        return {"status": "ERROR", "error": "worker stopped"}
                    if response.get("type") != "observation":
                        return {"status": "ERROR", "error": "invalid worker response"}
                    if response.get("ok") is not True:
                        return {
                            "status": "ERROR",
                            "error": dict(response.get("error", {}))
                            if isinstance(response.get("error"), Mapping)
                            else {"code": "CAPABILITY_EXCEPTION"},
                        }
                    return {
                        "status": "OK",
                        "sim_step_count": response.get("sim_step_count"),
                        "steps_added": response.get("steps_added"),
                    }

                try:
                    controller = run_task_demo_react(
                        client=controller_client,
                        robot_configuration_id=package.robot_configuration_id,
                        tasks=package.tasks,
                        design=design,
                        task_id=task_id,
                        public_arguments=public_arguments,
                        invoke=invoke,
                    )
                except Exception as exc:
                    controller_report = _controller_failure(exc)
                else:
                    controller_report = {
                        "kind": "fixed_react",
                        "status": controller.status,
                        "completed": True,
                        "model_turns": controller.model_turns,
                        "tool_calls": controller.tool_calls,
                        "capability_calls": controller.capability_calls,
                        "trace": [dict(item) for item in controller.trace],
                    }

                if worker_aborted:
                    raise HarnessError("ReAct candidate worker transport failed")
                if early_final is not None:
                    worker_result = early_final
                else:
                    _write_protocol(process, {"type": "finish"})
                    final = read_worker_response()
                    raw_result = final.get("result")
                    if final.get("type") != "final" or not isinstance(
                        raw_result, Mapping
                    ):
                        raise HarnessError("ReAct candidate worker final result is invalid")
                    worker_result = dict(raw_result)
        finally:
            if worker_result is None and process.poll() is None:
                process.kill()
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=wall_timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        if worker_result is None:
            stderr = process.stderr.read()[-1000:] if process.stderr is not None else ""
            raise HarnessError(f"ReAct candidate worker produced no result; stderr={stderr}")
        worker_result["controller"] = controller_report
        return worker_result


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


def _task_library_clauses(package: RobotPackage) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for task in package.tasks:
        task_id = str(task["task_id"])
        scoring = task.get("scoring")
        if not isinstance(scoring, list) or not scoring:
            raise HarnessError(f"Task Library task {task_id!r} has no scoring clauses")
        result[task_id] = {
            str(clause["clause_id"])
            for clause in scoring
            if isinstance(clause, Mapping) and isinstance(clause.get("clause_id"), str)
        }
        if len(result[task_id]) != len(scoring):
            raise HarnessError(f"Task Library task {task_id!r} has invalid scoring clauses")
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
    controller_client: ToolModelClient | None = None,
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
    use_task_demo_controller = (
        suite.get("artifact_type") == "task_demo_suite"
        and controller_client is not None
    )

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
                if use_task_demo_controller:
                    if not isinstance(public_arguments, Mapping):
                        raise HarnessError("Task Demo public_arguments must be an object")
                    assert controller_client is not None
                    worker = _run_react_worker(
                        payload,
                        candidate=candidate,
                        source_root=source_root,
                        wall_timeout_s=wall_timeout_s,
                        controller_client=controller_client,
                        package=package,
                        design=design,
                        task_id=str(case["task_id"]),
                        public_arguments=public_arguments,
                    )
                else:
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
            contact_integrity = _contact_integrity(worker)
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
            controller_value = worker.get("controller")
            controller_completed = (not use_task_demo_controller) or (
                isinstance(controller_value, Mapping)
                and bool(controller_value.get("completed"))
            )
            base_passed = (
                physical_execution_passed
                and bool(contact_integrity["passed"])
                and bool(guard_outcomes)
                and all(guard_outcomes.values())
                and video_evidence_passed
                and controller_completed
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
                    "controller": (
                        dict(worker["controller"])
                        if isinstance(worker.get("controller"), Mapping)
                        else None
                    ),
                    "candidate_log": worker.get("candidate_log", ""),
                    "controller_completed": controller_completed,
                    "physical_evidence": worker.get("physical_evidence", {}),
                    "physical_execution_passed": physical_execution_passed,
                    "contact_integrity": contact_integrity,
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
    designed_task_clauses = (
        _task_library_clauses(package)
        if suite.get("artifact_type") == "task_demo_suite"
        else _designed_task_clauses(design)
    )
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
    controller_trials = [
        trial["controller"]
        for trial in trials
        if isinstance(trial.get("controller"), Mapping)
    ]
    high_level_controller = {
        "kind": "fixed_react" if use_task_demo_controller else None,
        "path_enabled": use_task_demo_controller,
        "completed": bool(trials)
        and len(controller_trials) == len(trials)
        and all(bool(item.get("completed")) for item in controller_trials),
        "model_turn_count": sum(
            int(item.get("model_turns", 0)) for item in controller_trials
        ),
        "tool_call_count": sum(
            int(item.get("tool_calls", 0)) for item in controller_trials
        ),
        "capability_call_count": sum(
            int(item.get("capability_calls", 0)) for item in controller_trials
        ),
    }
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
        "high_level_controller": high_level_controller,
        "source_audit": {
            "capability_methods": list(source_audit.capability_methods),
            "imports_trusted_skeleton": source_audit.imports_trusted_skeleton,
        },
        "trials": trials,
    }
