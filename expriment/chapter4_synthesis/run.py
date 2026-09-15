#!/usr/bin/env python3
"""Prepare or run Chapter 4 synthesis through the maintained AA1 pipeline.

plan/check make no model requests. run starts fresh, sequential child processes;
summary reads their retained records without rerunning a model or a driver.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AA1 = ROOT / "AA1"
sys.path.insert(0, str(AA1))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def read_record(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        return {"error": f"cannot read {path.name}: {exc}"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_plan(cfg: dict, models=(), robots=(), replicates=()) -> dict:
    for selected, available, label in (
        (models, [m["id"] for m in cfg["models"]], "model"),
        (robots, [r["id"] for r in cfg["robots"]], "robot"),
        (replicates, range(1, cfg["replicates"] + 1), "replicate"),
    ):
        if set(selected) - set(available):
            raise ValueError(f"unknown {label} selection: {selected}")
    cells = []
    # Round first, then model and robot: avoid completing all three repeats
    # of one model before beginning the other models.
    for repeat in range(1, cfg["replicates"] + 1):
        for model in cfg["models"]:
            for robot in cfg["robots"]:
                if ((models and model["id"] not in models)
                        or (robots and robot["id"] not in robots)
                        or (replicates and repeat not in replicates)):
                    continue
                cells.append({"id": f"r{repeat:02d}_{model['id']}_{robot['id']}",
                              "replicate": repeat, "model": model, "robot": robot})
    if not cells or len({c["id"] for c in cells}) != len(cells):
        raise ValueError("the selected matrix must have non-empty, unique cell IDs")
    return {"configuration": cfg, "cells": cells}


def pipeline_config(cfg: dict, cell: dict, workspace_root: Path):
    from auto_adapter.orchestrator import SelfAssembleConfig
    from auto_adapter.robot_catalog import REPO_ROOT, find_robot_definition

    robot_id = cell["robot"]["catalog_id"]
    definition = find_robot_definition(robot_id)
    if not definition or definition.get("generation_route", "skeleton") != "skeleton":
        raise ValueError(f"{robot_id} is not a catalogued skeleton-route robot")
    generation = cfg["generation"]
    return SelfAssembleConfig(
        robot_id=robot_id, mjcf_path=REPO_ROOT / definition["mjcf"],
        workspace_root=workspace_root, bedrock_model=cell["model"]["model_id"],
        mode="local", model_provider=cell["model"].get("provider", generation["model_provider"]),
        aws_region=generation["aws_region"], enable_demo=False,
        capability_design_path=None, scene_cases_path=None,
        max_tokens_per_turn=generation["max_tokens_per_turn"],
        max_iters_study=generation["max_iters_study"],
        max_iters_capability_design=generation["max_iters_capability_design"],
        max_iters_generate=generation["max_iters_generate"],
        max_outer_gen_val_iters=1 + generation["max_repairs"],
    )


def check(plan: dict) -> dict:
    """Exercise actual assets, native physics and the existing video recorder."""
    import mujoco
    import numpy as np
    from auto_adapter.capability_design import task_library_for_robot
    from auto_adapter.design_validation import _Recorder
    from auto_adapter.robot_catalog import find_robot_definition, SKELETON_FOR_CLASS
    import auto_adapter.skeletons as skeletons

    checked = {}
    with tempfile.TemporaryDirectory(prefix="chapter4-synthesis-check-") as tmp:
        for cell in plan["cells"]:
            settings = pipeline_config(plan["configuration"], cell, Path(tmp) / cell["id"])
            robot_id = settings.robot_id
            if robot_id in checked:
                continue
            library = task_library_for_robot(robot_id)
            catalog = read_json(library / "catalog.json")
            if not catalog.get("tasks"):
                raise ValueError(f"{robot_id} has an empty task library")
            definition = find_robot_definition(robot_id)
            skeleton_name = definition.get("capability_skeleton") or SKELETON_FOR_CLASS[definition["class"]]
            getattr(skeletons, skeleton_name)
            if robot_id == "go2":
                weights = AA1 / "auto_adapter/skeletons/data/go2_velocity_policy.npz"
                with np.load(weights) as policy:
                    if not policy.files:
                        raise ValueError("Go2 policy archive is empty")
            model = mujoco.MjModel.from_xml_path(str(settings.mjcf_path))
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)
            recorder = _Recorder(model, data, {"max_sim_time_s": 0.1, "max_steps": 3,
                                               "video_fps": 1 / model.opt.timestep},
                                 Path(tmp) / f"{robot_id}.mp4")
            try:
                recorder.install()
                for _ in range(3):
                    mujoco.mj_step(model, data)
            finally:
                recorder.uninstall()
                video = recorder.finish()
            finite = all(np.isfinite(a).all() for a in (data.qpos, data.qvel, data.ctrl))
            if not finite or not video.get("ok"):
                raise RuntimeError(f"{robot_id} physics/video check failed: {video}")
            checked[robot_id] = {"mjcf": str(settings.mjcf_path), "skeleton": skeleton_name,
                                 "task_library": str(library), "tasks": len(catalog["tasks"]),
                                 "physics_steps": recorder.step_count,
                                 "recorded_samples": len(recorder.samples),
                                 "video_frames": video["frame_count"], "video_check_ok": True}
    return {"check": "offline assets, physics and video only", "model_requests": 0,
            "planned_cells": len(plan["cells"]), "endpoint_availability": "not tested",
            "robots": checked}


def worker(plan_path: Path, cell_id: str) -> int:
    from auto_adapter.orchestrator import SelfAssemble

    plan = read_json(plan_path)
    cell = next(c for c in plan["cells"] if c["id"] == cell_id)
    directory = plan_path.parent / cell_id
    workspace_root = directory / "generation"
    # The orchestrator clears current outputs on run(); never point it at a
    # previous attempt. A new replicate always gets a new worker/workspace.
    workspace_root.mkdir(exist_ok=False)
    result = {"cell_id": cell_id, "started_at": utc_now(), "pipeline": None, "error": None}
    started = time.monotonic()
    try:
        settings = pipeline_config(plan["configuration"], cell, workspace_root)
        with SelfAssemble(settings) as pipeline:
            result["pipeline"] = pipeline.run(stop_after="validate").to_json()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        result["finished_at"] = utc_now()
        result["duration_seconds"] = time.monotonic() - started
        write_json(directory / "result.json", result)
    # A completed pipeline may report failed driver validation. That is a
    # recorded outcome, not a reason to replace this draw with a fresh one.
    return 1 if result["error"] else 0


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def launch(plan_path: Path, cell: dict, timeout: float) -> dict:
    directory = plan_path.parent / cell["id"]
    directory.mkdir(exist_ok=False)
    record = {"started_at": utc_now(), "returncode": None, "timed_out": False, "interrupted": False}
    started = time.monotonic()
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", "--plan-file", str(plan_path),
             "--cell", cell["id"]], cwd=AA1, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            stop_worker(process)
        except KeyboardInterrupt:
            record["interrupted"] = True
            stop_worker(process)
        finally:
            record.update(returncode=process.returncode, finished_at=utc_now(),
                          duration_seconds=time.monotonic() - started)
            write_json(directory / "process.json", record)
    return record


def result_row(cell: dict, directory: Path) -> dict:
    process_path, result_path = directory / "process.json", directory / "result.json"
    process = read_record(process_path)
    result = read_record(result_path)
    pipeline = result.get("pipeline") or {}
    completed = (bool(process) and process.get("returncode") == 0
                 and not process.get("timed_out") and not process.get("interrupted")
                 and bool(pipeline) and not result.get("error"))
    phases = pipeline.get("phases", []) if completed else []
    validations = [p for p in phases if p["name"] in ("validate", "03_validate")
                   and p.get("metadata", {}).get("validation_report") is not None]
    final = validations[-1] if validations else None
    report = final["metadata"]["validation_report"] if final else {}
    own_suite_pass = None
    if final:
        own_suite_pass = (pipeline.get("stage1_ok") is True and pipeline.get("framework_ok") is True
                          and final.get("ok") is True and report.get("all_ok") is True
                          and report.get("n_total", 0) > 0)
    workspace = directory / "generation" / cell["robot"]["catalog_id"]
    errors = [p["error"] for p in phases if p.get("error")
              and not p["error"].startswith(("not run", "skipped"))]
    return {"cell_id": cell["id"], "model": cell["model"]["id"], "model_id": cell["model"]["model_id"],
            "robot": cell["robot"]["id"], "replicate": cell["replicate"],
            "launched": directory.exists(), "process_completed": completed,
            "pipeline_ok": own_suite_pass is True if completed else None,
            "driver_passes_own_suite": own_suite_pass,
            "cases_passed": report.get("n_passed"), "cases_tested": report.get("n_total"),
            "validation_attempts": len(validations) if completed else None,
            "duration_seconds": process.get("duration_seconds"),
            "input_tokens": sum(p.get("token_usage", {}).get("in", 0) for p in phases) if completed else None,
            "output_tokens": sum(p.get("token_usage", {}).get("out", 0) for p in phases) if completed else None,
            "timed_out": process.get("timed_out"), "interrupted": process.get("interrupted"),
            "error": result.get("error") or process.get("error")
                     or (errors[-1] if errors and own_suite_pass is not True else None),
            "workspace": str(workspace), "design_path": str(workspace / "design/capability_design.json"),
            "design_read_error": None}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(output_root: Path) -> dict:
    plan = read_json(output_root / "plan.json")
    rows = [result_row(cell, output_root / cell["id"]) for cell in plan["cells"]]
    criteria = []
    for row in rows:
        path = Path(row["design_path"])
        if not path.is_file():
            continue
        design = read_record(path)
        if design.get("error"):
            row["design_read_error"] = design["error"]
            continue
        for capability in design.get("capabilities", []):
            task_ids = [s["task_id"] for s in design.get("task_support", [])
                        if s["capability_id"] == capability["capability_id"]]
            for criterion in capability.get("criteria", []):
                item = {"cell_id": row["cell_id"], "capability_id": capability["capability_id"],
                        "method": capability["method_name"], "task_ids": json.dumps(task_ids)}
                for key in ("metric", "unit", "comparator", "threshold", "temporal", "aggregation", "source_refs"):
                    value = criterion.get(key)
                    item[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                criteria.append(item)
    summary = {"planned_cells": len(rows), "launched_cells": sum(r["launched"] for r in rows),
               "completed_processes": sum(r["process_completed"] for r in rows),
               "pipeline_passes": sum(r["pipeline_ok"] is True for r in rows),
               "drivers_passing_own_suite": sum(r["driver_passes_own_suite"] is True for r in rows),
               "note": "Counts do not assess criterion strictness. Missing and incomplete runs remain in the row table."}
    write_csv(output_root / "runs.csv", rows, list(rows[0]))
    write_csv(output_root / "criteria.csv", criteria,
              ["cell_id", "capability_id", "method", "task_ids", "metric", "unit", "comparator",
               "threshold", "temporal", "aggregation", "source_refs"])
    write_json(output_root / "summary.json", summary)
    return summary


def run_batch(plan: dict, output_root: Path) -> int:
    if output_root.exists():
        raise FileExistsError(f"use a new output directory; existing records are preserved: {output_root}")
    check_result = check(plan)
    output_root.mkdir(parents=True, exist_ok=False)
    write_json(output_root / "plan.json", plan)
    write_json(output_root / "check.json", check_result)
    process_errors = False
    for index, cell in enumerate(plan["cells"], 1):
        print(f"[{index}/{len(plan['cells'])}] {cell['id']}", flush=True)
        outcome = launch(output_root / "plan.json", cell, plan["configuration"]["trial_timeout_seconds"])
        summarize(output_root)
        if outcome["interrupted"]:
            return 130
        process_errors |= outcome["returncode"] != 0 or outcome["timed_out"]
    return 1 if process_errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "run", "summary", "_worker"))
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--robot", action="append", default=[])
    parser.add_argument("--replicate", type=int, action="append", default=[])
    parser.add_argument("--plan-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cell", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.command == "_worker":
        return worker(args.plan_file.resolve(), args.cell)
    if args.command in ("run", "summary") and args.output_root is None:
        parser.error("--output-root is required")
    if args.command == "summary":
        print(json.dumps(summarize(args.output_root.resolve()), indent=2))
        return 0
    plan = make_plan(read_json(args.config), args.model, args.robot, args.replicate)
    if args.command == "plan":
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if args.command == "check":
        print(json.dumps(check(plan), indent=2, ensure_ascii=False))
        return 0
    return run_batch(plan, args.output_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
