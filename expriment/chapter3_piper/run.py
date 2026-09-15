#!/usr/bin/env python3
"""Run the five-generation Chapter 3 PiPER experiment through the AA1 mainline.

`run` executes the complete configured cohort; `diagnostic RUN_ID` executes one
separate canary. Prepare/check/summarise never call a model. Task success comes
from the independent physical evaluator. Paths are resolved from this file.
"""
from __future__ import annotations

import argparse
import copy
import csv
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AA1 = ROOT / "AA1"
sys.path.insert(0, str(AA1))


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def config():
    return read_json(HERE / "config.json")


def episodes(cfg):
    """Expand three experiment-owned DEMOs into independent lateral layouts."""
    import yaml

    for task in cfg["tasks"]:
        source = yaml.safe_load((HERE / task["config"]).read_text())["robots"][cfg["robot_id"]]
        for layout, y in cfg["layouts"].items():
            demo = copy.deepcopy(source)
            params = demo["parameters"]
            params["target_position"][1] = y
            if "start_position" in params:
                params["start_position"][1] = y
                demo["initial_state"]["free_bodies"]["task_cube"]["position_m"][1] = y
            demo["scene"] = str(HERE / "prepared" / task["name"] / layout / "scene.xml")
            yield task, layout, demo


def vector(values):
    return " ".join(str(value) for value in values)


def episode_request(demo):
    return {"task_description": demo["task"], "parameters": demo["parameters"],
            "scene_path": demo["scene"], "initial_state": demo["initial_state"],
            "success_spec": demo["success"]}


def make_scene(cfg, task, demo, path):
    """Use the current robot XML and base-scene visuals, never old rollouts."""
    robot_path = ROOT / cfg["robot_model"]
    root = ET.parse(robot_path).getroot()
    root.set("model", "chapter3_piper_" + task["name"])
    compiler = root.find("compiler")
    compiler.set("meshdir", str((robot_path.parent / compiler.get("meshdir", "")).resolve()))
    for section in ET.parse(ROOT / cfg["base_scene"]).getroot():
        if section.tag == "include":
            continue
        target = root.find(section.tag)
        if target is None:
            root.append(section)
        else:
            target.extend(section)
    world = root.find("worldbody")
    s = cfg["scene"]
    table_half = s["table_half_size_m"]
    table = ET.SubElement(world, "body", name="chapter3_table_support",
                          pos=vector([*s["table_centre_xy_m"], s["table_top_z_m"] - table_half[2]]))
    ET.SubElement(table, "geom", name="chapter3_table", type="box",
                  size=vector(table_half), rgba="0.65 0.55 0.40 1")
    params = demo["parameters"]
    ET.SubElement(world, "site", name="task_goal", pos=vector(params["target_position"]),
                  size="0.008", rgba="0.1 0.9 0.2 0.7")
    if "start_position" in params:
        body = ET.SubElement(world, "body", name="task_cube", pos=vector(params["start_position"]))
        ET.SubElement(body, "freejoint", name="task_cube_free")
        ET.SubElement(body, "geom", name="task_cube_geom", type="box",
                      size=vector([s["cube_side_m"] / 2] * 3),
                      mass=str(s["cube_mass_kg"]), friction=vector(s["cube_friction"]),
                      condim=str(s["cube_condim"]), rgba="0.85 0.15 0.15 1")
    if task["name"] == "pick_place":
        goal = params["target_position"]
        half = s["platform_half_size_m"]
        platform = ET.SubElement(world, "body", name="receiving_platform_support",
                                 pos=vector([goal[0], goal[1], s["platform_top_z_m"] - half[2]]))
        ET.SubElement(platform, "geom", name="receiving_platform", type="box",
                      size=vector(half), rgba="0.2 0.4 0.8 1")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="unicode")


def prepare(cfg):
    import mujoco
    import numpy as np
    import yaml
    from auto_adapter.scene_runtime import apply_initial_state

    records = []
    for task, layout, demo in episodes(cfg):
        folder = HERE / "prepared" / task["name"] / layout
        scene = folder / "scene.xml"
        make_scene(cfg, task, demo, scene)
        model = mujoco.MjModel.from_xml_path(str(scene))
        if any(model.eq_type == mujoco.mjtEq.mjEQ_WELD):
            raise ValueError(f"unexpected weld constraint in {scene}")
        data = mujoco.MjData(model)
        apply_initial_state(model, data, {"initial_state": demo["initial_state"]})
        if not np.isfinite(data.qpos).all():
            raise ValueError(f"non-finite initial state in {scene}")
        for binding in demo["success"]["bindings"].values():
            getattr(model, binding["kind"])(binding["name"])
        (folder / "demo.yaml").write_text(yaml.safe_dump(
            {"robots": {cfg["robot_id"]: demo}}, sort_keys=False))
        write_json(folder / "episode.json", episode_request(demo))
        records.append({"task_id": task["id"], "layout": layout,
                        "scene_path": str(scene), "nq": model.nq, "nu": model.nu,
                        "timestep_s": model.opt.timestep, "initial_qpos": data.qpos.tolist(),
                        "initial_ee_position_m": data.site("ee_site").xpos.tolist()})
    write_json(HERE / "prepared" / "scene_check.json", {
        "scope": "scene compilation and initialisation only; no motion feasibility or task verdict",
        "scenes": records})
    (HERE / "data" / "runs").mkdir(parents=True, exist_ok=True)
    (HERE / "data" / "diagnostics").mkdir(parents=True, exist_ok=True)
    print(f"Prepared {len(records)} scenes; no model calls or task episodes executed.")


def check(cfg):
    actual = {"python": sys.version.split()[0]}
    missing = []
    for package in ("mujoco", "numpy", "mcp", "PyYAML", "imageio", "imageio-ffmpeg"):
        try:
            actual[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            missing.append(package)
    mismatches = {name: {"expected": expected, "actual": actual.get(name)}
                  for name, expected in cfg["versions"].items() if actual.get(name) != expected}
    from auto_adapter.agent.recap import RecapBudgets
    from auto_adapter.robot_catalog import SKELETON_FOR_CLASS, find_robot_definition
    defaults = RecapBudgets()
    budget_ok = (defaults.max_planning_turns == cfg["budgets"]["recap_planning_calls"]
                 and defaults.max_capability_calls == cfg["budgets"]["recap_capability_calls"])
    library = read_json(ROOT / cfg["task_library"])
    definition = find_robot_definition(robot_id=cfg["robot_id"])
    skeleton = (definition or {}).get("capability_skeleton") or SKELETON_FOR_CLASS.get((definition or {}).get("class"))
    generation_ok = (cfg["generation_mode"] == "skeleton-assisted" and bool(definition)
                     and definition.get("generation_route", "skeleton") == "skeleton"
                     and skeleton == "ArmSerialDLSSkeleton"
                     and (AA1 / definition["mjcf"]).resolve() == (ROOT / cfg["base_scene"]).resolve())
    sampling_ok = cfg["model"].get("temperature") is None and cfg["model"].get("reasoning_effort") is None
    mcp_version = tuple(int(part) for part in actual.get("mcp", "0.0").split(".")[:2])
    mcp_ok = (1, 28) <= mcp_version < (2, 0)
    success_checks = check_demo_success(cfg)
    report = {"interpreter": sys.executable, "versions": actual, "missing_packages": missing,
              "version_mismatches": mismatches, "recap_budgets_match": budget_ok,
              "generation_route_matches": generation_ok, "skeleton": skeleton,
              "sampling_parameters_match": sampling_ok, "mcp_version_supported": mcp_ok,
              "model": cfg["model"], "budgets": cfg["budgets"],
              "design_catalogue_tasks": len(library["tasks"]),
              "downstream_tasks": len(cfg["tasks"]), "layouts_per_task": len(cfg["layouts"]),
              "planned_generation_runs": len(cfg["run_ids"]),
              "demo_success_checks": success_checks,
              "formal_run_prerequisites": [
                  "Independent runtime-owned contact/trajectory recording and task scoring",
                  "Motion reachability, collision and grasp feasibility of the proposed layouts",
                  "Case immutability and withheld-layout access boundaries",
                  "Explicit confirmation of the executable experiment protocol"],
              "network_or_model_access_tested": False}
    print(json.dumps(report, indent=2))
    return (not missing and not mismatches and budget_ok and generation_ok and sampling_ok and mcp_ok
            and all(item["ok"] for item in success_checks))


def check_demo_success(cfg):
    """Exercise each exact success config on a fresh, stationary MuJoCo trace.

    This detects unsupported criteria before generation calls a model. It is an
    interface/initial-state check, not task execution or motion feasibility.
    """
    try:
        import mujoco
        from auto_adapter.demo_trace import DemoTrace
        from task_scoring import install_experiment_scoring
        from auto_adapter.demo_evaluation import evaluate_demo_task
        from auto_adapter.scene_runtime import apply_initial_state
        install_experiment_scoring()
    except ImportError as exc:
        return [{"ok": False, "error": f"mainline DEMO scorer unavailable: {exc}"}]

    checks = []
    with tempfile.TemporaryDirectory(prefix="ch3-demo-check-") as directory:
        for task, layout, demo in episodes(cfg):
            item = {"task": task["name"], "layout": layout, "ok": False}
            try:
                prepared = HERE / "prepared" / task["name"] / layout / "episode.json"
                if not prepared.is_file() or read_json(prepared) != episode_request(demo):
                    raise ValueError("prepared DEMO differs from source config; run prepare")
                model = mujoco.MjModel.from_xml_path(demo["scene"])
                data = mujoco.MjData(model)
                apply_initial_state(model, data, {"initial_state": demo["initial_state"]})
                path = Path(directory) / f"{task['name']}-{layout}.jsonl"
                trace = DemoTrace(model, data, demo["success"]["bindings"], path)
                try:
                    mujoco.mj_step(model, data)
                finally:
                    trace_report = trace.close()
                if trace_report["error"]:
                    raise ValueError(trace_report["error"])
                samples = [json.loads(line) for line in path.read_text().splitlines()]
                verdict = evaluate_demo_task(demo["success"], demo["parameters"], samples)
                item["ok"] = (verdict.get("physical_task_success") is False
                              and not verdict.get("evaluation_error"))
                item["stationary_task_success"] = verdict.get("physical_task_success")
                if not item["ok"]:
                    item["error"] = verdict.get("evaluation_error") or "stationary scene must not pass"
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
            checks.append(item)
    return checks


def run_trial(cfg, run_id, data_root, *, diagnostic=False):
    """One fresh generation session, followed by all nine eligible episodes."""
    from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig
    import yaml

    run_dir = Path(data_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "config.json", cfg)
    b, m = cfg["budgets"], cfg["model"]
    if b["repair"] != b["generate"] or b["repair"] > 22:
        raise ValueError("current AA1 Repair budget cannot represent this configuration")
    outcome = {"run_id": run_id,
               "scope": "diagnostic, excluded from experiment cohort" if diagnostic else "Chapter 3 PiPER experiment",
               "driver_validation_pass": False, "export_ok": False,
               "downstream_eligible": False, "episodes": []}
    started = time.monotonic()
    try:
        settings = SelfAssembleConfig(
            robot_id=cfg["robot_id"], mjcf_path=ROOT / cfg["base_scene"],
            workspace_root=run_dir / "generation", bedrock_model=m["id"],
            model_provider=m["provider"], aws_region=m["region"],
            max_iters_study=b["study"], max_iters_capability_design=b["design"],
            max_iters_generate=b["generate"], max_iters_export=b["export"],
            max_outer_gen_val_iters=b["max_repairs"] + 1,
            max_tokens_per_turn=m["max_tokens"], enable_demo=False)
        with (run_dir / "generation.log").open("w") as log:
            from contextlib import redirect_stdout, redirect_stderr
            with redirect_stdout(log), redirect_stderr(log), SelfAssemble(settings) as pipeline:
                result = pipeline.run(stop_after="export")
        outcome["generation"] = result.to_json()
        workspace = run_dir / "generation" / cfg["robot_id"]
        report_path = workspace / "validate_report.json"
        validation = read_json(report_path) if report_path.is_file() else {}
        outcome["driver_validation_pass"] = validation.get("all_ok") is True
        outcome["export_ok"] = any(p.name == "04_export" and p.ok for p in result.phases)
        if not (result.ok and outcome["driver_validation_pass"] and outcome["export_ok"]):
            outcome["downstream_not_executed_reason"] = "generation, full-suite validation or export failed"
            return
        outcome["downstream_eligible"] = True
        design = read_json(workspace / "design" / "capability_design.json")
        suite = yaml.safe_load((workspace / "design" / "scene_cases.yaml").read_text())
        driver = workspace / "driver.py"
        original_code = driver.read_bytes()
        for task, layout, _ in episodes(cfg):
            print(f"{run_id}: DEMO {task['name']}/{layout}", flush=True)
            request = read_json(HERE / "prepared" / task["name"] / layout / "episode.json")
            folder = run_dir / "episodes" / task["name"] / layout
            request.update(driver_path=str(driver), export_server_path=str(workspace / "mcp_server.py"),
                           robot_id=cfg["robot_id"], capability_design=design,
                           validation_suite=suite, validation_report=validation,
                           output_dir=str(folder / "task"), model=m["id"], provider=m["provider"],
                           region=m["region"], max_tokens=m["max_tokens"], from_scratch=False)
            write_json(folder / "request.json", request)
            (folder / "demo.yaml").write_text(
                (HERE / "prepared" / task["name"] / layout / "demo.yaml").read_text())
            env = dict(os.environ, PYTHONPATH=str(AA1))
            with (folder / "worker.log").open("w") as log:
                try:
                    child = subprocess.run([sys.executable, str(HERE / "demo_worker.py"),
                                            "--input-json", str(folder / "request.json")],
                                           cwd=AA1, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=2400, check=False)
                    worker_result = {"returncode": child.returncode}
                except subprocess.TimeoutExpired:
                    worker_result = {"error": "task worker exceeded the existing 2400 s wall timeout"}
            if driver.read_bytes() != original_code:
                raise RuntimeError("source driver changed during downstream evaluation")
            task_report = folder / "task" / "task_report.json"
            outcome["episodes"].append({"task_id": task["id"], "layout": layout,
                                        "report_path": str(task_report), **worker_result})
            write_json(run_dir / "run_result.json", outcome)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        outcome["duration_sec"] = time.monotonic() - started
        write_json(run_dir / "run_result.json", outcome)


def run_experiment(cfg):
    """Execute every planned generation once; preserve failures in the cohort."""
    root = HERE / "data" / "runs"
    existing = [run_id for run_id in cfg["run_ids"] if (root / run_id).exists()]
    if existing:
        raise FileExistsError(f"experiment run directories already exist: {existing}; no runs overwritten or skipped")
    prepare(cfg)
    if not check(cfg):
        raise RuntimeError("experiment preflight failed; no model request was made")
    try:
        for index, run_id in enumerate(cfg["run_ids"], 1):
            print(f"Starting {run_id} ({index}/{len(cfg['run_ids'])})", flush=True)
            try:
                run_trial(cfg, run_id, root)
            except Exception as exc:
                # run_trial preserves the current attempt before raising. An
                # early failure does not remove a run or replace it with a retry.
                record = root / run_id / "run_result.json"
                if not record.is_file():
                    write_json(record, {"run_id": run_id, "scope": "Chapter 3 PiPER experiment",
                                        "driver_validation_pass": False, "export_ok": False,
                                        "downstream_eligible": False, "episodes": [],
                                        "error": f"{type(exc).__name__}: {exc}"})
                print(f"{run_id} ended with {type(exc).__name__}: {exc}", flush=True)
            print(f"Recorded {run_id}", flush=True)
    finally:
        summarise(cfg, root)


def run_diagnostic(cfg, run_id):
    root = HERE / "data" / "diagnostics"
    if (root / run_id).exists():
        raise FileExistsError(f"diagnostic directory already exists: {root / run_id}")
    prepare(cfg)
    if not check(cfg):
        raise RuntimeError("diagnostic preflight failed; no model request was made")
    try:
        run_trial(cfg, run_id, root, diagnostic=True)
    finally:
        summarise(cfg, root, diagnostic=True)


def physical_verdict(report):
    """Require an actual independent verdict, never transport/controller `ok`."""
    value = report.get("physical_task_success")
    if report.get("evaluation_error") or not isinstance(report.get("task_metrics"), list):
        return None
    return value if type(value) is bool else None


def summarise(cfg, data_root=None, *, diagnostic=False):
    root = Path(data_root) if data_root else HERE / "data" / ("diagnostics" if diagnostic else "runs")
    rows, run_rows = [], []
    for run_id in cfg["run_ids"]:
        result_path = root / run_id / "run_result.json"
        result = read_json(result_path) if result_path.is_file() else {}
        run_rows.append({"run_id": run_id, "run_record_present": result_path.is_file(),
                         "driver_validation_pass": result.get("driver_validation_pass", ""),
                         "export_ok": result.get("export_ok", ""),
                         "downstream_eligible": result.get("downstream_eligible", ""),
                         "duration_sec": result.get("duration_sec", ""),
                         "error": result.get("error", ""),
                         "downstream_not_executed_reason": result.get("downstream_not_executed_reason", "")})
        attempted = {(episode["task_id"], episode["layout"]) for episode in result.get("episodes", [])}
        for task, layout, _ in episodes(cfg):
            path = root / run_id / "episodes" / task["name"] / layout / "task" / "task_report.json"
            report = read_json(path) if path.is_file() else {}
            verdict = physical_verdict(report)
            rows.append({"run_id": run_id, "task_id": task["id"], "layout": layout,
                         "episode_attempted": (task["id"], layout) in attempted,
                         "task_report_present": path.is_file(),
                         "controller_status": report.get("status", "not executed"),
                         "physical_task_success": "" if verdict is None else verdict,
                         "evaluation_error": report.get("evaluation_error", ""),
                         "evaluation_path": report.get("evaluation_path", ""),
                         "physics_samples_path": report.get("physics_samples_path", ""),
                         "report_path": str(path) if path.is_file() else ""})
    counts = {"scope": "diagnostic, excluded from experiment cohort" if diagnostic else "Chapter 3 PiPER experiment",
              "planned_generation_runs": len(cfg["run_ids"]),
              "recorded_runs": sum(row["run_record_present"] for row in run_rows),
              "driver_validation_passes": sum(row["driver_validation_pass"] is True for row in run_rows),
              "export_passes": sum(row["export_ok"] is True for row in run_rows),
              "eligible_drivers": sum(row["downstream_eligible"] is True for row in run_rows),
              "planned_task_slots": len(rows),
              "attempted_task_episodes": sum(row["episode_attempted"] for row in rows),
              "task_reports": sum(row["task_report_present"] for row in rows),
              "independently_scored_task_reports": sum(type(row["physical_task_success"]) is bool for row in rows),
              "physical_task_successes": sum(row["physical_task_success"] is True for row in rows),
              "physical_task_failures": sum(row["physical_task_success"] is False for row in rows)}
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "summary.json", counts)
    for name, records in (("runs.csv", run_rows), ("episodes.csv", rows)):
        with (root / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    print(json.dumps(counts, indent=2))
    return counts


def main():
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "run", "diagnostic", "summarise"))
    parser.add_argument("run_id", nargs="?", choices=cfg["run_ids"])
    parser.add_argument("--diagnostic", action="store_true",
                        help="read diagnostic data with summarise; use 'diagnostic RUN_ID' for a single canary")
    args = parser.parse_args()
    if args.command == "diagnostic" and not args.run_id:
        parser.error("diagnostic requires RUN_ID")
    if args.run_id and args.command != "diagnostic":
        parser.error("run executes all five runs; a single trial uses diagnostic RUN_ID")
    if args.diagnostic and args.command != "summarise":
        parser.error("--diagnostic selects diagnostic summaries only")
    if args.command == "check":
        return 0 if check(cfg) else 1
    if args.command == "prepare":
        prepare(cfg)
    elif args.command == "summarise":
        summarise(cfg, diagnostic=args.diagnostic)
    elif args.command == "diagnostic":
        run_diagnostic(cfg, args.run_id)
    else:
        run_experiment(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
