#!/usr/bin/env python3
"""Run the approved Chapter 3 supplementary process cases.

``check`` only loads the maintained AA1 assets and exercises the fixed DEMO
bindings on two native MuJoCo steps.  ``run`` performs the five configured
generation attempts once, then runs the fixed Astra DEMO only for an exported
driver whose complete Framework validation passed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AA1 = ROOT / "AA1"
sys.path.insert(0, str(AA1))

CONFIG_PATH = HERE / "config.json"
DATA_ROOT = HERE / "data" / "runs"
DEMO_CONFIG_PATH = AA1 / "auto_adapter" / "demo_tasks.yaml"


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_config() -> dict:
    return read_json(CONFIG_PATH)


def load_demos() -> dict:
    import yaml

    document = yaml.safe_load(DEMO_CONFIG_PATH.read_text(encoding="utf-8"))
    robots = document.get("robots") if isinstance(document, dict) else None
    if not isinstance(robots, dict):
        raise ValueError("AA1 demo_tasks.yaml has no robots mapping")
    return robots


def resolve_aa1_path(value: str) -> Path:
    path = Path(value)
    return (AA1 / path).resolve() if not path.is_absolute() else path.resolve()


def generation_settings(cfg: dict) -> dict:
    generation = cfg["generation"]
    return {
        "max_iters_study": generation["max_iters_study"],
        "max_iters_capability_design": generation["max_iters_capability_design"],
        "max_iters_generate": generation["max_iters_generate"],
        "max_iters_export": generation["max_iters_export"],
        "max_outer_gen_val_iters": generation["max_outer_gen_val_iters"],
        "max_tokens_per_turn": generation["max_tokens_per_turn"],
        "mode": generation["mode"],
        "model_provider": generation["model_provider"],
        "aws_region": generation["aws_region"],
    }


def _config_checks(cfg: dict) -> dict:
    generation = cfg.get("generation", {})
    recap = cfg.get("recap", {})
    expected_generation = {
        "max_iters_study": 16,
        "max_iters_capability_design": 30,
        "max_iters_generate": 22,
        "max_iters_export": 8,
        "max_outer_gen_val_iters": 4,
        "max_tokens_per_turn": 8000,
        "mode": "local",
        "model_provider": "holistic",
        "aws_region": "eu-west-2",
    }
    actual_generation = {
        key: generation.get(key) for key in expected_generation
    }
    expected_recap = {
        "model": "global.openai.gpt-6-astra",
        "provider": "holistic",
        "region": "eu-west-2",
        "max_tokens": 8000,
        "planning_calls_limit": 16,
        "capability_calls_limit": 12,
    }
    actual_recap = {key: recap.get(key) for key in expected_recap}
    return {
        "generation_matches_approved_budget": actual_generation == expected_generation,
        "generation": actual_generation,
        "recap_matches_fixed_astra": actual_recap == expected_recap,
        "recap": actual_recap,
        "runs_per_condition": cfg.get("runs_per_condition"),
        "condition_count": len(cfg.get("conditions", [])),
    }


def _finite_state(data, np) -> bool:
    return all(
        np.isfinite(values).all()
        for values in (data.qpos, data.qvel, data.ctrl)
    )


def _library_observation(robot_id: str) -> dict:
    from auto_adapter.capability_design import task_library_for_robot

    library = task_library_for_robot(robot_id)
    catalog_path = library / "catalog.json"
    catalog = read_json(catalog_path)
    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"task library for {robot_id} has no tasks")
    return {
        "path": str(library),
        "catalog_path": str(catalog_path),
        "package_version": catalog.get("package_version"),
        "task_count": len(tasks),
    }


def _go2_policy_observation() -> dict:
    metadata_path = AA1 / "auto_adapter" / "skeletons" / "data" / "go2_velocity_policy.json"
    weights_path = metadata_path.with_suffix(".npz")
    metadata = read_json(metadata_path)
    if not weights_path.is_file() or weights_path.stat().st_size <= 0:
        raise ValueError(f"Go2 policy weights are missing or empty: {weights_path}")
    return {
        "metadata_path": str(metadata_path),
        "weights_path": str(weights_path),
        "artifact_id": metadata.get("artifact_id"),
        "weights_bytes": weights_path.stat().st_size,
    }


def _demo_binding_check(robot_id: str, demo: dict, mujoco, np, output_dir: Path) -> dict:
    from auto_adapter.demo_evaluation import evaluate_demo_task
    from auto_adapter.demo_trace import DemoTrace
    from auto_adapter.scene_runtime import apply_initial_state

    scene = resolve_aa1_path(demo["scene"])
    model = mujoco.MjModel.from_xml_path(str(scene))
    data = mujoco.MjData(model)
    apply_initial_state(model, data, {"initial_state": demo["initial_state"]})
    initial_finite = _finite_state(data, np)
    trace_path = output_dir / f"{robot_id}.jsonl"
    trace = DemoTrace(model, data, demo["success"]["bindings"], trace_path)
    try:
        mujoco.mj_step(model, data)
        mujoco.mj_step(model, data)
    finally:
        trace_report = trace.close()
    samples = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    evaluation = evaluate_demo_task(
        demo["success"], demo["parameters"], samples
    )
    finite_samples = all(
        np.isfinite(value).all()
        for sample in samples
        for value in (
            np.asarray(sample["state"][label].get("position", sample["state"][label].get("value")), dtype=float)
            if "position" in sample["state"][label]
            else np.asarray([sample["state"][label]["value"]], dtype=float)
            for label in sample["state"]
        )
    )
    return {
        "scene_path": str(scene),
        "nq": model.nq,
        "nu": model.nu,
        "initial_state_finite": initial_finite,
        "trace": trace_report,
        "sample_count": len(samples),
        "samples_finite": finite_samples,
        "evaluation": evaluation,
        # This is a binding/scoring-parser check; it is deliberately not a
        # task-success gate and must never be reported as readiness evidence.
        "binding_check_ok": bool(
            initial_finite
            and finite_samples
            and trace_report["sample_count"] >= 2
            and trace_report["error"] is None
            and evaluation.get("evaluation_error") is None
        ),
    }


def check(cfg: dict) -> bool:
    """Run the offline catalog, skeleton, scene, and DEMO parser preflight."""
    import importlib

    import mujoco
    import numpy as np
    from auto_adapter.robot_catalog import (
        REPO_ROOT,
        SKELETON_FOR_CLASS,
        find_robot_definition,
    )
    import auto_adapter.skeletons as skeletons

    condition_robot_ids = [item["robot_id"] for item in cfg["conditions"]]
    unique_robot_ids = list(dict.fromkeys(condition_robot_ids))
    demos = load_demos()
    robots = []
    with tempfile.TemporaryDirectory(prefix="chapter3-process-check-") as temp_dir:
        trace_dir = Path(temp_dir)
        for robot_id in unique_robot_ids:
            definition = find_robot_definition(robot_id=robot_id)
            if not definition:
                raise ValueError(f"robot {robot_id!r} is absent from the AA1 catalog")
            generation_scene = (REPO_ROOT / definition["mjcf"]).resolve()
            if not generation_scene.is_file():
                raise ValueError(f"catalog MJCF is missing: {generation_scene}")
            demo = demos.get(robot_id)
            if not isinstance(demo, dict):
                raise ValueError(f"AA1 fixed DEMO is missing for {robot_id!r}")
            demo_scene = resolve_aa1_path(demo["scene"])
            if not demo_scene.is_file():
                raise ValueError(f"fixed DEMO scene is missing: {demo_scene}")

            generation_model = mujoco.MjModel.from_xml_path(str(generation_scene))
            generation_data = mujoco.MjData(generation_model)
            mujoco.mj_forward(generation_model, generation_data)
            generation_finite = _finite_state(generation_data, np)

            skeleton_name = definition.get("capability_skeleton") or SKELETON_FOR_CLASS.get(
                definition.get("class")
            )
            if not skeleton_name:
                raise ValueError(f"no configured skeleton for {robot_id!r}")
            skeleton_type = getattr(skeletons, skeleton_name)
            importlib.import_module(skeleton_type.__module__)

            item = {
                "robot_id": robot_id,
                "catalog_class": definition.get("class"),
                "generation_mjcf": str(generation_scene),
                "generation_model": {
                    "nq": generation_model.nq,
                    "nu": generation_model.nu,
                    "finite_state": generation_finite,
                },
                "demo_scene": str(demo_scene),
                "skeleton": skeleton_name,
                "skeleton_module": skeleton_type.__module__,
                "task_library": _library_observation(robot_id),
            }
            if robot_id == "go2":
                item["go2_policy"] = _go2_policy_observation()
            item["demo"] = _demo_binding_check(
                robot_id, demo, mujoco, np, trace_dir
            )
            robots.append(item)

    report = {
        "scope": "offline MuJoCo load/binding check only; no policy rollout, model call, or task-success claim",
        "interpreter": sys.executable,
        "config": _config_checks(cfg),
        "conditions": [item["id"] for item in cfg["conditions"]],
        "unique_robots": unique_robot_ids,
        "network_or_model_accessed": False,
        "robots": robots,
    }
    print(json.dumps(report, indent=2, allow_nan=False))
    return bool(
        report["config"]["generation_matches_approved_budget"]
        and report["config"]["recap_matches_fixed_astra"]
        and cfg.get("runs_per_condition") == 1
        and all(item["generation_model"]["finite_state"] for item in robots)
        and all(item["demo"]["binding_check_ok"] for item in robots)
    )


def _demo_request(cell_dir: Path, condition: dict, workspace: Path) -> tuple[Path, Path, Path]:
    recap = load_config()["recap"]
    output_dir = cell_dir / "demo"
    request_path = cell_dir / "demo_request.json"
    request = {
        "workspace": str(workspace),
        "robot_id": condition["robot_id"],
        "model": recap["model"],
        "provider": recap["provider"],
        "region": recap["region"],
        "max_tokens": recap["max_tokens"],
        "demo_config_path": str(DEMO_CONFIG_PATH),
        "output_dir": str(output_dir),
        "export_server_path": str(workspace / "mcp_server.py"),
    }
    write_json(request_path, request)
    return request_path, output_dir, cell_dir / "demo_worker.log"


def _run_demo(cell_dir: Path, condition: dict, workspace: Path) -> dict:
    request_path, output_dir, log_path = _demo_request(cell_dir, condition, workspace)
    driver = workspace / "driver.py"
    original_driver = driver.read_bytes()
    environment = dict(os.environ)
    # The child and its stdio MCP server must import the maintained AA1 tree.
    environment["PYTHONPATH"] = str(AA1)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [sys.executable, str(HERE / "demo_worker.py"),
                 "--input-json", str(request_path)],
                cwd=AA1,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=2400,
                check=False,
            )
        process = {"returncode": completed.returncode}
    except subprocess.TimeoutExpired:
        process = {"error": "DEMO worker exceeded the existing 2400 s wall timeout"}
    if driver.read_bytes() != original_driver:
        raise RuntimeError("generated driver source changed during DEMO")

    report_path = output_dir / "task_report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    return {
        **process,
        "request_path": str(request_path),
        "worker_log": str(log_path),
        "report_path": str(report_path),
        "physical_task_success": report.get("physical_task_success"),
        "evaluation_error": report.get("evaluation_error"),
        "video_path": report.get("video_path"),
        "physics_samples_path": report.get("physics_samples_path"),
        "trace_path": report.get("trace_path"),
    }


def run_condition(cfg: dict, condition: dict) -> dict:
    condition_id = condition["id"]
    cell_dir = DATA_ROOT / condition_id
    cell_dir.mkdir(parents=True, exist_ok=False)
    write_json(cell_dir / "config.json", cfg)
    started = time.monotonic()
    outcome = {
        "condition": condition,
        "runs_per_condition": 1,
        "generation": None,
        "framework_ok": False,
        "export_ok": False,
        "validation_all_ok": False,
        "demo": None,
    }
    try:
        from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig
        from auto_adapter.robot_catalog import REPO_ROOT, find_robot_definition

        definition = find_robot_definition(robot_id=condition["robot_id"])
        if not definition:
            raise ValueError(f"robot {condition['robot_id']!r} is absent from the AA1 catalog")
        settings = generation_settings(cfg)
        self_assemble_config = SelfAssembleConfig(
            robot_id=condition["robot_id"],
            mjcf_path=REPO_ROOT / definition["mjcf"],
            workspace_root=cell_dir / "generation",
            bedrock_model=condition["bedrock_model"],
            mode=settings["mode"],
            model_provider=settings["model_provider"],
            aws_region=settings["aws_region"],
            max_iters_study=settings["max_iters_study"],
            max_iters_capability_design=settings["max_iters_capability_design"],
            max_iters_generate=settings["max_iters_generate"],
            max_iters_export=settings["max_iters_export"],
            max_outer_gen_val_iters=settings["max_outer_gen_val_iters"],
            max_tokens_per_turn=settings["max_tokens_per_turn"],
            enable_demo=False,
        )
        generation_log = cell_dir / "generation.log"
        with generation_log.open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                with SelfAssemble(self_assemble_config) as pipeline:
                    result = pipeline.run(stop_after="export")
        outcome["generation"] = result.to_json()
        workspace = cell_dir / "generation" / condition["robot_id"]
        validation_path = workspace / "validate_report.json"
        validation = read_json(validation_path) if validation_path.is_file() else {}
        export_phase = next(
            (phase for phase in result.phases if phase.name == "04_export"), None
        )
        outcome["framework_ok"] = bool(result.framework_ok)
        outcome["export_ok"] = bool(export_phase is not None and export_phase.ok)
        outcome["validation_all_ok"] = validation.get("all_ok") is True

        if not (
            outcome["framework_ok"]
            and export_phase is not None
            and export_phase.ok
            and outcome["validation_all_ok"]
        ):
            outcome["demo_skipped_reason"] = (
                "DEMO requires the final Framework validation and 04_export to pass"
            )
        else:
            driver = workspace / "driver.py"
            design = workspace / "design" / "capability_design.json"
            suite = workspace / "design" / "scene_cases.yaml"
            for artifact in (driver, design, suite, workspace / "mcp_server.py"):
                if not artifact.is_file():
                    raise FileNotFoundError(f"required exported artifact is missing: {artifact}")
            outcome["demo"] = _run_demo(cell_dir, condition, workspace)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        outcome["duration_sec"] = time.monotonic() - started
        write_json(cell_dir / "run_result.json", outcome)
    return outcome


def run_experiment(cfg: dict) -> int:
    if not check(cfg):
        raise RuntimeError("offline process-case preflight failed; no model request was made")
    if DATA_ROOT.exists():
        if not DATA_ROOT.is_dir() or any(DATA_ROOT.iterdir()):
            raise FileExistsError(
                f"data/runs is not fresh; refusing to overwrite existing records: {DATA_ROOT}"
            )
    else:
        DATA_ROOT.mkdir(parents=True)
    for index, condition in enumerate(cfg["conditions"], 1):
        print(f"Starting {condition['id']} ({index}/{len(cfg['conditions'])})", flush=True)
        outcome = run_condition(cfg, condition)
        if outcome.get("error"):
            print(f"Recorded {condition['id']} with error: {outcome['error']}", flush=True)
        else:
            print(f"Recorded {condition['id']}", flush=True)
    return 0


def main() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run"))
    args = parser.parse_args()
    if args.command == "check":
        return 0 if check(cfg) else 1
    return run_experiment(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
