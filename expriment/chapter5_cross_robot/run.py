#!/usr/bin/env python3
"""Chapter 5 generation through AA1's real Study/Design/Generate/Validate path.

plan/check make no model calls. run launches isolated sequential trials;
summary reads saved outcomes without rerunning them.
"""
from __future__ import annotations

import argparse
import csv
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


def make_plan(config: dict, robots=(), replicates=()) -> dict:
    ids = [robot["id"] for robot in config["robots"]]
    if set(robots) - set(ids):
        raise ValueError(f"unknown robot selection: {robots}")
    if set(replicates) - set(range(1, config["replicates"] + 1)):
        raise ValueError(f"unknown replicate selection: {replicates}")
    from auto_adapter.capability_design import task_library_for_robot
    from auto_adapter.robot_catalog import find_robot_definition, SKELETON_FOR_CLASS

    inputs = {}
    for robot in config["robots"]:
        robot_id = robot["id"]
        definition = find_robot_definition(robot_id)
        if definition is None:
            raise ValueError(f"robot is not registered in the maintained catalog: {robot_id}")
        library = task_library_for_robot(robot_id)
        inputs[robot_id] = {
            **robot, "mjcf": str((AA1 / definition["mjcf"]).relative_to(ROOT)),
            "task_library": str(library.relative_to(ROOT)),
            "task_count": len(read_json(library / "catalog.json")["tasks"]),
            "generation_route": definition.get("generation_route", "skeleton"),
            "skeleton": definition.get("capability_skeleton") or SKELETON_FOR_CLASS.get(definition["class"]),
        }
    cells = [{"id": f"{robot_id}_r{repeat:02d}", "robot": robot_id, "replicate": repeat}
             for repeat in range(1, config["replicates"] + 1)
             for robot_id in ids
             if (not robots or robot_id in robots) and (not replicates or repeat in replicates)]
    if not cells:
        raise ValueError("the selected experiment contains no trials")
    return {"configuration": config, "inputs": inputs, "cells": cells}


def pipeline_config(plan: dict, cell: dict, workspace: Path):
    from auto_adapter.orchestrator import SelfAssembleConfig

    cfg, robot_id = plan["configuration"], cell["robot"]
    common = dict(
        robot_id=robot_id, mjcf_path=ROOT / plan["inputs"][robot_id]["mjcf"],
        workspace_root=workspace, bedrock_model=cfg["model"]["id"],
        model_provider=cfg["model"]["provider"], aws_region=cfg["model"]["region"],
        mode="local", enable_demo=False, capability_design_path=None, scene_cases_path=None,
    )
    generation = cfg["generation"]
    route = plan["inputs"][robot_id]["generation_route"]
    if route == "skeleton":
        return SelfAssembleConfig(**common, **generation)
    if route == "from_scratch":
        from auto_adapter.orchestrator_from_scratch import FromScratchConfig
        return FromScratchConfig(
            **common, max_tokens_per_turn=generation["max_tokens_per_turn"],
            max_iters_study=generation["max_iters_study"],
            max_iters_capability_design=generation["max_iters_capability_design"],
            max_iters_gen_algo=generation["max_iters_generate"],
            max_iters_gen_repair=generation["max_iters_generate"],
            max_outer_retries=generation["max_outer_gen_val_iters"] - 1,
        )
    raise ValueError(f"unsupported catalog generation route: {route}")


def check(plan: dict) -> dict:
    """Load the actual robot inputs and exercise native physics/video locally."""
    import mujoco
    import numpy as np
    import auto_adapter.skeletons as skeletons
    from auto_adapter.design_validation import _Recorder

    checked = {}
    with tempfile.TemporaryDirectory(prefix="chapter5-check-") as tmp:
        for cell in plan["cells"]:
            robot_id = cell["robot"]
            if robot_id in checked:
                continue
            settings = pipeline_config(plan, cell, Path(tmp) / cell["id"])
            entry = plan["inputs"][robot_id]
            if entry["generation_route"] == "skeleton":
                getattr(skeletons, entry["skeleton"])
            if not entry["task_count"]:
                raise ValueError(f"empty public task library for {robot_id}")
            if robot_id == "go2":
                with np.load(AA1 / "auto_adapter/skeletons/data/go2_velocity_policy.npz") as weights:
                    if not weights.files or not all(np.isfinite(weights[k]).all() for k in weights.files):
                        raise ValueError("Go2 policy weights are empty or non-finite")
            model = mujoco.MjModel.from_xml_path(str(settings.mjcf_path))
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)
            recorder = _Recorder(model, data,
                                 {"max_sim_time_s": 0.1, "max_steps": 3,
                                  "video_fps": 1 / model.opt.timestep},
                                 Path(tmp) / f"{robot_id}.mp4")
            try:
                recorder.install()
                for _ in range(3):
                    mujoco.mj_step(model, data)
            finally:
                recorder.uninstall()
                video = recorder.finish()
            if not all(np.isfinite(a).all() for a in (data.qpos, data.qvel, data.ctrl)) or not video["ok"]:
                raise RuntimeError(f"physics or video check failed for {robot_id}: {video}")
            checked[robot_id] = {**entry, "nq": model.nq, "nv": model.nv, "nu": model.nu,
                                 "physics_steps": recorder.step_count, "video_frames": video["frame_count"]}
    return {"check": "local assets, native MuJoCo and video only", "model_requests": 0,
            "planned_trials": len(plan["cells"]), "robots": checked,
            "versions": {"python": sys.version.split()[0], "mujoco": mujoco.__version__,
                         "numpy": np.__version__}}


def worker(plan_path: Path, cell_id: str) -> int:
    from auto_adapter.orchestrator import SelfAssemble
    from auto_adapter.orchestrator_from_scratch import FromScratchOrchestrator

    plan = read_json(plan_path)
    cell = next(cell for cell in plan["cells"] if cell["id"] == cell_id)
    directory = plan_path.parent / cell_id
    workspace = directory / "generation"
    workspace.mkdir(exist_ok=False)
    result = {"cell_id": cell_id, "pipeline": None, "error": None}
    try:
        implementation = (SelfAssemble if plan["inputs"][cell["robot"]]["generation_route"] == "skeleton"
                          else FromScratchOrchestrator)
        with implementation(pipeline_config(plan, cell, workspace)) as pipeline:
            result["pipeline"] = pipeline.run(
                stop_after=plan["configuration"].get("stop_after", "validate")
            ).to_json()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        write_json(directory / "result.json", result)
    return 1 if result["error"] else 0


def result_row(plan: dict, cell: dict, directory: Path) -> dict:
    def saved(name):
        path = directory / name
        return read_json(path) if path.is_file() else {}

    result, process = saved("result.json"), saved("process.json")
    pipeline = result.get("pipeline") or {}
    phases = pipeline.get("phases", [])
    completed = (process.get("returncode") == 0 and not process.get("interrupted")
                 and not process.get("timed_out") and bool(pipeline) and not result.get("error"))
    validations = [phase for phase in phases if phase["name"] in ("validate", "03_validate")
                   and not (phase.get("error") or "").startswith(("not run", "skipped"))]
    final = validations[-1] if validations else {}
    report = final.get("metadata", {}).get("validation_report", pipeline.get("validate_report", {}))
    passed = None
    if completed and final:
        passed = (pipeline.get("stage1_ok") is True and pipeline.get("framework_ok") is True
                  and final.get("ok") is True and report.get("all_ok") is True
                  and report.get("n_total", 0) > 0 and report.get("n_passed") == report["n_total"])
    failures = [phase for phase in phases if phase.get("ok") is False
                and not (phase.get("error") or "").startswith(("not run", "skipped"))]
    failure = failures[-1] if failures and passed is not True else {}
    pipeline_error = pipeline.get("error") if passed is not True else None
    tokens = pipeline.get("total_tokens") or {
        key: sum(p.get("token_usage", {}).get(key, 0) for p in phases) for key in ("in", "out")
    }
    return {"trial": cell["id"], "robot": cell["robot"], "replicate": cell["replicate"],
            "model": plan["configuration"]["model"]["id"],
            "generation_route": plan["inputs"][cell["robot"]]["generation_route"],
            "launched": directory.exists(),
            "process_completed": completed, "driver_passes_own_suite": passed,
            "cases_passed": report.get("n_passed"), "cases_tested": report.get("n_total"),
            "validation_submissions": len(validations),
            "failure_stage": failure.get("name"),
            "error": result.get("error") or pipeline_error or failure.get("error"),
            "duration_seconds": process.get("duration_seconds"),
            "input_tokens": tokens.get("in") if phases else None,
            "output_tokens": tokens.get("out") if phases else None,
            "timed_out": process.get("timed_out"), "interrupted": process.get("interrupted"),
            "workspace": str(directory / "generation" / cell["robot"])}


def summarize(output: Path) -> dict:
    plan = read_json(output / "plan.json")
    rows = [result_row(plan, cell, output / cell["id"]) for cell in plan["cells"]]
    with (output / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for robot in dict.fromkeys(cell["robot"] for cell in plan["cells"]):
        selected = [row for row in rows if row["robot"] == robot]
        counts[robot] = {"planned": len(selected), "launched": sum(r["launched"] for r in selected),
                         "drivers_passing_own_suite": sum(r["driver_passes_own_suite"] is True for r in selected)}
    summary = {"robots": counts, "trials": rows,
               "note": "Each driver is judged against its own generated suite. Review task coverage and criteria separately; case denominators are not pooled."}
    write_json(output / "summary.json", summary)
    return summary


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            process.wait()


def run_batch(plan: dict, output: Path) -> int:
    if output.exists():
        raise FileExistsError(f"use a new output directory; existing records are preserved: {output}")
    checked = check(plan)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    write_json(output / "check.json", checked)
    summarize(output)
    errors = False
    for index, cell in enumerate(plan["cells"], 1):
        directory = output / cell["id"]
        directory.mkdir()
        print(f"[{index}/{len(plan['cells'])}] {cell['id']}", flush=True)
        record = {"timed_out": False, "interrupted": False, "returncode": None}
        started = time.monotonic()
        with (directory / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_worker",
                                        "--plan-file", str(output / "plan.json"), "--cell", cell["id"]],
                                       cwd=AA1, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=plan["configuration"]["trial_timeout_seconds"])
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                stop_worker(process)
            except KeyboardInterrupt:
                record["interrupted"] = True
                stop_worker(process)
            finally:
                record.update(returncode=process.returncode, duration_seconds=time.monotonic() - started)
                write_json(directory / "process.json", record)
                summarize(output)
        if record["interrupted"]:
            return 130
        errors |= record["timed_out"] or record["returncode"] != 0
    return int(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "run", "summary", "_worker"))
    parser.add_argument("--config", type=Path, default=HERE / "configs/franka.json")
    parser.add_argument("--robot", action="append", default=[])
    parser.add_argument("--replicate", type=int, action="append", default=[])
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--plan-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cell", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.command == "_worker":
        return worker(args.plan_file.resolve(), args.cell)
    if args.command in ("run", "summary") and not args.output_root:
        parser.error("--output-root is required")
    if args.command == "summary":
        print(json.dumps(summarize(args.output_root.resolve())["robots"], indent=2))
        return 0
    plan = make_plan(read_json(args.config), args.robot, args.replicate)
    if args.command in ("plan", "check"):
        print(json.dumps(plan if args.command == "plan" else check(plan), indent=2))
        return 0
    return run_batch(plan, args.output_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
