#!/usr/bin/env python3
"""Export an existing driver and run the selected robot's experiment tasks."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

from run import AA1, HERE, read_json, stop_worker, write_json


def task_functions(robot_id: str):
    if robot_id == "franka":
        from robots.franka.scenes import prepare_cases, check_cases
        from robots.franka.task_evaluation import evaluate_franka_task
        return prepare_cases, check_cases, evaluate_franka_task
    if robot_id == "leap_hand":
        from robots.leap_hand.scenes import prepare_cases, check_cases
        from robots.leap_hand.task_evaluation import evaluate_leap_task
        return prepare_cases, check_cases, evaluate_leap_task
    if robot_id == "h1":
        from robots.h1.scenes import prepare_cases, check_cases
        from robots.h1.task_evaluation import evaluate_h1_task
        return prepare_cases, check_cases, evaluate_h1_task
    if robot_id == "skydio_x2":
        from robots.skydio_x2.scenes import prepare_cases, check_cases
        from robots.skydio_x2.task_evaluation import evaluate_skydio_task
        return prepare_cases, check_cases, evaluate_skydio_task
    raise ValueError(f"no experiment task implementation for {robot_id!r}")


def export_existing(workspace: Path, *, allow_failed_validation: bool = False,
                    configuration: dict | None = None) -> dict:
    from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig
    from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator
    from auto_adapter.robot_catalog import find_robot_definition

    record_path = workspace / "standalone_export.json"
    if record_path.exists() or (workspace / "mcp_server.py").exists():
        raise FileExistsError("this workspace already has a standalone export attempt or server")
    configuration = configuration or read_json(HERE / "configs/franka.json")
    design = read_json(workspace / "design/capability_design.json")
    definition = find_robot_definition(design["robot_configuration_id"])
    if definition is None:
        raise ValueError("export robot is not registered in the maintained catalog")
    from_scratch = definition.get("generation_route", "skeleton") == "from_scratch"
    driver_name = "driver_from_scratch.py" if from_scratch else "driver.py"
    protected = {name: (workspace / name).read_bytes() for name in (
        driver_name, "validate_report.json", "design/capability_design.json",
        "design/scene_cases.yaml")}
    settings = dict(
        robot_id=design["robot_configuration_id"],
        mjcf_path=(workspace / "mjcf.xml").resolve(), workspace_root=workspace.parent,
        bedrock_model=configuration["model"]["id"],
        model_provider=configuration["model"]["provider"],
        aws_region=configuration["model"]["region"], mode="local", enable_demo=False,
        max_tokens_per_turn=configuration["generation"]["max_tokens_per_turn"],
    )
    cfg = (FromScratchConfig(**settings) if from_scratch else SelfAssembleConfig(**settings))
    implementation = FromScratchOrchestrator if from_scratch else SelfAssemble
    with implementation(cfg) as pipeline:
        pipeline.capability_design = design
        phase = (pipeline.phase_export() if from_scratch else
                 pipeline._phase_export(allow_failed_validation=allow_failed_validation))
    unchanged = all((workspace / name).read_bytes() == content
                    for name, content in protected.items())
    record = {"ok": phase.ok and unchanged, "error": phase.error,
              "allow_failed_validation": allow_failed_validation,
              "from_scratch": from_scratch,
              "source_files_unchanged": unchanged, "duration_seconds": phase.duration_sec,
              "token_usage": phase.token_usage, "metadata": phase.metadata,
              "trace_path": str(phase.trace_path)}
    if not unchanged:
        record["error"] = "export modified the driver, design or original validation record"
    write_json(record_path, record)
    return record


def prepare(workspace: Path, output: Path, *, allow_failed_validation: bool = False,
            model: str | None = None, max_tokens: int = 6000,
            recap_budgets: dict | None = None, task_timeout_seconds: int = 900,
            configuration: dict | None = None) -> dict:
    from auto_adapter.agent.recap import RecapBudgets
    from auto_adapter.export_support import validate_dynamic_export_source
    from auto_adapter.scene_runtime import load_scene_cases
    from auto_adapter.robot_catalog import find_robot_definition

    if output.exists():
        raise FileExistsError(f"use a new task output directory: {output}")
    configuration = configuration or read_json(HERE / "configs/franka.json")
    if len(configuration["robots"]) != 1:
        raise ValueError("task preparation requires a single-robot configuration")
    robot_id = configuration["robots"][0]["id"]
    definition = find_robot_definition(robot_id)
    if definition is None:
        raise ValueError(f"robot is not registered in the maintained catalog: {robot_id}")
    from_scratch = definition.get("generation_route", "skeleton") == "from_scratch"
    driver_name = "driver_from_scratch.py" if from_scratch else "driver.py"
    for name in (driver_name, "mcp_server.py", "validate_report.json",
                 "design/capability_design.json", "design/scene_cases.yaml"):
        if not (workspace / name).is_file():
            raise FileNotFoundError(workspace / name)
    prepare_cases, check_cases, _ = task_functions(robot_id)
    design = read_json(workspace / "design/capability_design.json")
    report = read_json(workspace / "validate_report.json")
    validation_passed = (report.get("all_ok") is True
                         and report.get("n_total", 0) > 0
                         and report.get("n_passed") == report["n_total"])
    if not (design.get("robot_configuration_id") == robot_id
            and report.get("n_total", 0) > 0
            and (validation_passed or allow_failed_validation)):
        raise ValueError(f"tasks require the {robot_id} driver's nonempty successful validation report")
    validate_dynamic_export_source(workspace / "mcp_server.py", design)
    suite = load_scene_cases(workspace / "design/scene_cases.yaml", design=design)
    selected_model = model or configuration["downstream_plan"]["model"]
    budgets = asdict(RecapBudgets(**(recap_budgets or {})))
    if max_tokens <= 0 or task_timeout_seconds <= 0:
        raise ValueError("model token and task timeout budgets must be positive")
    output.mkdir(parents=True, exist_ok=False)
    cases = prepare_cases(output / "scenes")
    write_json(output / "scene_check.json", check_cases(cases))
    downstream = configuration["downstream_plan"]
    expected_tasks = (downstream["candidate_task_ids"] if robot_id == "leap_hand"
                      else [task["id"] for task in downstream["tasks"]])
    if list(dict.fromkeys(case["task_id"] for case in cases)) != expected_tasks:
        raise ValueError("prepared tasks differ from the configured task selection")
    requests = output / "requests"
    requests.mkdir()
    for case in cases:
        payload = {
            "driver_path": str(workspace / driver_name), "robot_id": robot_id,
            "from_scratch": from_scratch,
            "capability_design": design, "validation_suite": suite,
            "validation_report": report, "export_server_path": str(workspace / "mcp_server.py"),
            "task_description": case["task_description"], "scene_path": case["scene_path"],
            "initial_state": case["initial_state"], "parameters": case["parameters"],
            "success_spec": case["success_spec"], "output_dir": str(output / case["id"]),
            "model": selected_model, "recap_budgets": budgets,
            "provider": configuration["model"]["provider"],
            "region": configuration["model"]["region"], "max_tokens": max_tokens,
        }
        write_json(requests / f"{case['id']}.json", payload)
    plan = {"generation_workspace": str(workspace), "robot_id": robot_id,
            "from_scratch": from_scratch,
            "model": selected_model,
            "max_tokens": max_tokens, "recap_budgets": budgets,
            "task_timeout_seconds": task_timeout_seconds, "cases": cases,
            "allow_failed_validation": allow_failed_validation,
            "source_validation": {key: report.get(key) for key in ("all_ok", "n_passed", "n_total")}}
    write_json(output / "task_plan.json", plan)
    summarize(output)
    return plan


def summarize(output: Path) -> dict:
    plan = read_json(output / "task_plan.json")
    rows = []
    for case in plan["cases"]:
        directory = output / case["id"]
        report_path = directory / "task_report.json"
        process_path = output / "requests" / f"{case['id']}.process.json"
        report = read_json(report_path) if report_path.is_file() else {}
        process = read_json(process_path) if process_path.is_file() else {}
        completed = bool(process and not process.get("timed_out") and not process.get("interrupted")
                         and process.get("returncode") in (0, 1) and report)
        rows.append({
            "case": case["id"], "task": case["task_id"], "launched": directory.exists(),
            "completed": completed, "execution_ok": report.get("execution_ok"),
            "physical_task_success": report.get("physical_task_success"),
            "task_success": bool(completed and process.get("returncode") == 0
                                 and report.get("ok") is True
                                 and report.get("execution_ok") is True
                                 and report.get("physical_task_success") is True),
            "error": report.get("error") or report.get("evaluation_error"),
            "timed_out": process.get("timed_out"), "interrupted": process.get("interrupted"),
            "duration_seconds": process.get("duration_seconds"), "video": report.get("video_path"),
            "report": str(report_path) if report else None,
        })
    with (output / "tasks.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {"planned": len(rows), "completed": sum(row["completed"] for row in rows),
               "succeeded": sum(row["task_success"] for row in rows), "cases": rows,
               "allow_failed_validation": plan.get("allow_failed_validation", False),
               "source_validation": plan.get("source_validation"),
               "note": f"These {len(rows)} downstream cases share one generated driver; they are not generation replicates."}
    write_json(output / "task_summary.json", summary)
    return summary


def task_worker(request: Path) -> int:
    """Use the maintained ReCAP/MCP runner with this experiment's own scorer."""
    from auto_adapter.agent.task_execution import run_task

    payload = read_json(request)
    _, _, evaluator = task_functions(payload["robot_id"])
    report = run_task(**payload, task_evaluator=evaluator)
    print(json.dumps({key: report.get(key) for key in ("ok", "error", "video_path", "report_path")}))
    return 0 if report["ok"] else 1


def execute(output: Path) -> int:
    plan = read_json(output / "task_plan.json")
    for index, case in enumerate(plan["cases"], 1):
        request = output / "requests" / f"{case['id']}.json"
        process_path = request.with_suffix(".process.json")
        log_path = request.with_suffix(".log")
        if (output / case["id"]).exists() or process_path.exists() or log_path.exists():
            raise FileExistsError(f"task already attempted: {case['id']}")
        print(f"[{index}/{len(plan['cases'])}] {case['id']}", flush=True)
        started = time.monotonic()
        record = {"timed_out": False, "interrupted": False}
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_worker", "--request-json", str(request)],
                cwd=AA1, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=plan["task_timeout_seconds"])
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                stop_worker(process)
            except KeyboardInterrupt:
                record["interrupted"] = True
                stop_worker(process)
            finally:
                record.update(returncode=process.returncode, duration_seconds=time.monotonic() - started)
                write_json(process_path, record)
                summary = summarize(output)
        print(f"  physical success: {summary['cases'][index - 1]['physical_task_success']}", flush=True)
        if record["interrupted"]:
            return 130
    return 0 if summary["succeeded"] == summary["planned"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "prepare", "run", "summary", "_worker"))
    parser.add_argument("--config", type=Path, default=HERE / "configs/franka.json")
    parser.add_argument("--generation-workspace", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--request-json", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--model", help="ReCAP-only model override for a new task output directory")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-planning-turns", type=int)
    parser.add_argument("--max-capability-calls", type=int)
    parser.add_argument("--context-window-messages", type=int)
    parser.add_argument("--task-timeout-seconds", type=int)
    parser.add_argument("--allow-failed-validation", action="store_true",
                        help="use an existing export despite failed validation; skeleton export also supports this override")
    args = parser.parse_args()
    if args.command == "_worker":
        if not args.request_json:
            parser.error("_worker requires --request-json")
        return task_worker(args.request_json.resolve())
    if args.command == "export":
        if not args.generation_workspace:
            parser.error("export requires --generation-workspace")
        record = export_existing(args.generation_workspace.resolve(),
                                 allow_failed_validation=args.allow_failed_validation,
                                 configuration=read_json(args.config))
        print(json.dumps(record))
        return 0 if record["ok"] else 1
    if not args.output_root:
        parser.error("--output-root is required")
    output = args.output_root.resolve()
    if args.command == "summary":
        summary = summarize(output)
        print(json.dumps({key: summary[key] for key in ("planned", "completed", "succeeded")}))
        return 0
    if args.command == "prepare":
        if not args.generation_workspace:
            parser.error("prepare requires --generation-workspace")
        configuration = read_json(args.config)
        downstream = configuration["downstream_plan"]
        defaults = {"max_planning_turns": 16, "max_capability_calls": 12,
                    "context_window_messages": 32, **downstream.get("recap_budgets", {})}
        budgets = {key: getattr(args, key) if getattr(args, key) is not None else value
                   for key, value in defaults.items()}
        plan = prepare(args.generation_workspace.resolve(), output,
                       allow_failed_validation=args.allow_failed_validation,
                       configuration=configuration,
                       model=args.model,
                       max_tokens=(args.max_tokens if args.max_tokens is not None
                                   else downstream.get("max_tokens", 6000)),
                       task_timeout_seconds=(args.task_timeout_seconds
                           if args.task_timeout_seconds is not None
                           else downstream.get("task_timeout_seconds", 900)),
                       recap_budgets=budgets)
        print(f"Prepared {len(plan['cases'])} explicit task requests; no model calls made.")
        return 0
    return execute(output)


if __name__ == "__main__":
    raise SystemExit(main())
