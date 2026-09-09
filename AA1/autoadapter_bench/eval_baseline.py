#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a BASELINE (not our TaskPlanner) on the same task suite as eval.py.

Usage:
    python autoadapter_bench/eval_baseline.py \
        --baseline cap \
        --robot so101 --suites simple \
        --model us.anthropic.claude-sonnet-4-6 \
        --n-trials 3 \
        --output autoadapter_bench/results/cap_so101_sonnet45.json

The output JSON has the SAME schema as eval.py so leaderboard.py picks it
up. The only differences:
  - meta.baseline      — set to baseline name (cap, rl, vla, random, ours)
  - meta.framework      — describes the per-task engineering cost
                          ("CaP: human pre-built API" vs "Ours: agent bootstrapped")

For CaP and ours (drop-in for direct comparison), the planner must expose
.execute_task(prompt, task_id) returning an object with the standard fields.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Reuse eval.py's helpers
sys.path.insert(0, str(REPO_ROOT / "autoadapter_bench"))
from eval import (  # noqa: E402
    load_robot_zoo, load_task_suite, find_robot,
    find_driver_workspace, evaluate_success, _replay_tool_calls,
    _build_aggregate, _cost_usd,
)


# Baseline registry — maps name → (PlannerClass, description)
def _build_baselines() -> dict:
    """Return baseline class objects (imported lazily)."""
    from auto_adapter.agent.task_planner import TaskPlanner  # noqa: PLC0415
    from autoadapter_bench.baselines.code_as_policies import CaPPlanner  # noqa: PLC0415
    return {
        "ours": (TaskPlanner, "ours: agent bootstraps driver from MJCF"),
        "cap":  (CaPPlanner, "Code-as-Policies: LLM composes pre-built APIs"),
    }


# ──────────────────────────────────────────────────────────────────────────
# Trial runner
# ──────────────────────────────────────────────────────────────────────────


class _TrialTimeout(BaseException):
    """Raised by the SIGALRM handler when a single trial exceeds --trial-timeout.
    Inherits BaseException (like KeyboardInterrupt) so the broad `except
    Exception` blocks inside ReactLoop / the CaP runner do NOT swallow it —
    the watchdog must unwind all the way back to the trial loop."""


def _alarm_handler(signum, frame):  # noqa: ARG001
    raise _TrialTimeout()


def _timeout_trial_dict(trial_idx: int, secs: int) -> dict:
    """Fallback trial record when a trial is cut off by --trial-timeout.
    Counts as a physics failure (the policy did not complete in the budget),
    mirroring the crash-fallback shape so aggregation is unaffected."""
    return {
        "trial": trial_idx, "llm_ok": False, "physics_ok": False,
        "agreement": True, "n_tool_calls": 0, "frames": 0,
        "duration_sec": float(secs), "tokens": {},
        "summary": "", "error": f"trial_timeout_{secs}s",
        "physics_detail": "trial_timeout", "physics_metrics": {},
    }


def run_one_trial(planner, task: dict, trial_idx: int) -> dict:
    """Execute one task trial + physics validation. Mirrors eval.run_task
    semantics but for a single trial.
    """
    import numpy as np  # noqa: PLC0415

    skel = planner._load_driver()
    if hasattr(skel, "home") and callable(getattr(skel, "home")):
        try:
            skel.home()
        except Exception:
            pass

    before_state: dict = {}
    if hasattr(skel, "get_ee_pose"):
        try:
            ee, _ = skel.get_ee_pose()
            before_state["ee_pose"] = ee.tolist()
        except Exception:
            pass
    if hasattr(skel, "get_base_pose"):
        try:
            p, _ = skel.get_base_pose()
            before_state["base_pose_xyz"] = p.tolist()
        except Exception:
            pass
    if hasattr(skel, "get_body_height"):
        try:
            before_state["body_height"] = float(skel.get_body_height())
        except Exception:
            pass
    objects = {}
    # Known bodies: SO-101 v2 scene + pushbench scenes
    for b in ("banana", "mug", "bottle", "screwdriver", "duck", "lego",
              "tee", "obs", "cube_red", "cube_green", "cube_blue"):
        try:
            objects[b] = skel.get_object_position(b).tolist()
        except Exception:
            pass
    before_state["objects"] = objects

    try:
        r = planner.execute_task(task["prompt"], task_id=f"{task['id']}_t{trial_idx}")
        crash = None
    except Exception as e:  # noqa: BLE001
        r = None
        crash = f"{type(e).__name__}: {e}"

    if r is None:
        return {
            "trial": trial_idx, "llm_ok": False, "physics_ok": False,
            "agreement": True, "n_tool_calls": 0, "frames": 0,
            "duration_sec": 0.0, "tokens": {},
            "summary": "", "error": crash,
            "physics_detail": "crash", "physics_metrics": {},
        }

    # Physics check on a fresh skel via tool-call replay
    replay_skel = planner._load_driver()
    if hasattr(replay_skel, "home") and callable(getattr(replay_skel, "home")):
        try:
            replay_skel.home()
        except Exception:
            pass
    phase_snaps: list = []
    replay_clean = _replay_tool_calls(replay_skel, r.tool_call_log, phase_snaps)
    before_state["_phase_snapshots"] = phase_snaps
    try:
        physics_ok, detail, metrics = evaluate_success(
            replay_skel, before_state, task["success"], r, task["id"]
        )
    except Exception as e:  # noqa: BLE001
        physics_ok = False
        detail = f"physics eval exception: {type(e).__name__}: {e}"
        metrics = {}
    if not replay_clean:
        detail = f"REPLAY DIVERGED. {detail}"
        metrics["replay_diverged"] = True

    return {
        "trial": trial_idx,
        "llm_ok": bool(r.ok),
        "physics_ok": bool(physics_ok),
        "agreement": (r.ok == physics_ok),
        "n_tool_calls": r.n_tool_calls,
        "frames": r.n_frames,
        "duration_sec": r.duration_sec,
        "tokens": r.token_usage,
        "summary": (r.summary or "")[:300],
        "error": r.error,
        "physics_detail": detail,
        "physics_metrics": metrics,
    }


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True,
                        choices=list(_build_baselines()))
    parser.add_argument("--robot", required=True)
    parser.add_argument("--suites", default="simple")
    parser.add_argument("--model",
                        default="us.anthropic.claude-sonnet-4-6")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--n-trials", type=int, default=None)
    parser.add_argument("--trial-timeout", type=int, default=None,
                        help="Per-trial wall-clock cap (sec). A trial exceeding "
                             "this is recorded as a physics failure and the run "
                             "continues. Use to bound pathological slow cells.")
    parser.add_argument("--tasks", default=None,
                        help="Comma-separated task IDs")
    parser.add_argument("--output", required=True)
    parser.add_argument("--driver-workspace", default=None,
                        help="Override driver auto-locate (e.g. v2 quad "
                             "workspace artifacts/from_scratch_quad_v2/go2).")
    parser.add_argument("--few-shot", action="store_true",
                        help="CaP only: prepend few-shot exemplar programs "
                             "to the system prompt (CaP-faithful robustness "
                             "variant; default is zero-shot).")
    parser.add_argument("--resume", action="store_true",
                        help="If output exists, skip already-completed tasks.")
    args = parser.parse_args()

    baselines = _build_baselines()
    PlannerClass, baseline_desc = baselines[args.baseline]

    zoo = load_robot_zoo()
    robot = find_robot(zoo, args.robot)
    if args.driver_workspace:
        workspace = Path(args.driver_workspace).resolve()
        if not ((workspace / "driver.py").exists()
                or (workspace / "driver_from_scratch.py").exists()):
            raise SystemExit(f"--driver-workspace {workspace} has no driver.py / "
                             "driver_from_scratch.py")
    else:
        workspace = find_driver_workspace(args.robot)
    print(f"Baseline: {args.baseline}  ({baseline_desc})")
    print(f"Robot: {robot['id']}  class: {robot['class']}  workspace: {workspace}")

    real_mjcf = (REPO_ROOT / robot["mjcf"]).resolve()
    mjcf_link = workspace / "mjcf.xml"
    if not (mjcf_link.exists() and mjcf_link.is_symlink()
            and Path(os.readlink(mjcf_link)).resolve() == real_mjcf):
        if mjcf_link.exists() or mjcf_link.is_symlink():
            mjcf_link.unlink()
        mjcf_link.symlink_to(real_mjcf)

    suite_yaml = load_task_suite(robot["class"])
    requested = [s.strip() for s in args.suites.split(",") if s.strip()]
    task_filter = (set(t.strip() for t in args.tasks.split(","))
                   if args.tasks else None)

    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL).decode().strip()[:12]
    except Exception:
        git_sha = "unknown"

    # ─── Resume / checkpoint support ─────────────────────────────
    output_path = Path(args.output)
    resume_completed: set[tuple[str, str]] = set()
    result: dict | None = None
    if output_path.exists() and getattr(args, "resume", False):
        try:
            result = json.loads(output_path.read_text())
            for s_name, s_data in result.get("suites", {}).items():
                for t in s_data.get("tasks", []):
                    if len(t.get("trials", [])) >= t.get("n_trials", 0):
                        resume_completed.add((s_name, t["id"]))
            print(f"[resume] loaded {output_path.name} — skipping "
                  f"{len(resume_completed)} already-completed tasks")
        except Exception as e:  # noqa: BLE001
            print(f"[resume] failed to load {output_path}: {e}; starting fresh")
            result = None

    if result is None:
        result = {
            "meta": {
                "benchmark_version": "v1.0",
                "baseline": args.baseline,
                "baseline_description": baseline_desc,
                "few_shot": bool(args.few_shot),
                "robot": robot["id"],
                "robot_class": robot["class"],
                "mjcf": robot["mjcf"],
                "vendor": robot["vendor"],
                "dof": robot["dof"],
                "model": args.model,
                "region": args.region,
                "git_sha": git_sha,
                "started_at": datetime.utcnow().isoformat(),
                "suites_requested": requested,
            },
            "suites": {},
        }

    def _checkpoint() -> None:
        _build_aggregate(result)  # keep aggregate consistent on every write
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))

    t_start = time.time()
    grand_in = grand_out = 0
    for s_data in result.get("suites", {}).values():
        for t in s_data.get("tasks", []):
            grand_in += t.get("tokens", {}).get("in", 0)
            grand_out += t.get("tokens", {}).get("out", 0)

    # Construct planner per baseline (different __init__ signatures)
    if args.baseline == "ours":
        planner_kwargs = {
            "workspace": workspace,
            "bedrock_model": args.model,
            "region": args.region,
            "max_iters": 30, "max_tokens_per_turn": 6000,
        }
    elif args.baseline == "cap":
        planner_kwargs = {
            "workspace": workspace,
            "bedrock_model": args.model,
            "region": args.region,
            "max_tokens": 4000,
            "run_tag": Path(args.output).stem,
            "few_shot": args.few_shot,
        }
    else:
        raise ValueError(args.baseline)

    with PlannerClass(**planner_kwargs) as planner:
        for suite_name in requested:
            if suite_name not in suite_yaml["suites"]:
                print(f"[skip] unknown suite {suite_name!r}")
                continue
            suite = suite_yaml["suites"][suite_name]
            print(f"\n=== suite: {suite_name} — {len(suite['tasks'])} tasks ===")
            existing_suite = result["suites"].get(suite_name, {"tasks": []})
            suite_results = list(existing_suite.get("tasks", []))
            for task in suite["tasks"]:
                if task_filter and task["id"] not in task_filter:
                    continue
                if (suite_name, task["id"]) in resume_completed:
                    print(f"  [skip-resume] {task['id']} already complete")
                    continue
                suite_results = [t for t in suite_results if t["id"] != task["id"]]
                n_trials = args.n_trials or task["success"].get("n_trials", 1)
                print(f"  task: {task['id']}  ({n_trials} trials)")
                trials = []
                for ti in range(n_trials):
                    print(f"    trial {ti+1}/{n_trials}: ", end="", flush=True)
                    if args.trial_timeout:
                        signal.signal(signal.SIGALRM, _alarm_handler)
                        signal.alarm(args.trial_timeout)
                    try:
                        tr = run_one_trial(planner, task, ti)
                    except _TrialTimeout:
                        tr = _timeout_trial_dict(ti, args.trial_timeout)
                        print("TIMEOUT ", end="")
                    finally:
                        if args.trial_timeout:
                            signal.alarm(0)
                    trials.append(tr)
                    agree = "✓" if tr["agreement"] else "✗"
                    print(f"llm={'OK' if tr['llm_ok'] else 'FAIL'} "
                          f"phys={'OK' if tr['physics_ok'] else 'FAIL'} {agree}")
                n_llm = sum(1 for t in trials if t["llm_ok"])
                n_phys = sum(1 for t in trials if t["physics_ok"])
                n_agree = sum(1 for t in trials if t["agreement"])
                in_t = sum(t["tokens"].get("in", 0) for t in trials)
                out_t = sum(t["tokens"].get("out", 0) for t in trials)
                grand_in += in_t; grand_out += out_t
                suite_results.append({
                    "id": task["id"],
                    "prompt": task["prompt"],
                    "success_spec": task["success"],
                    "n_trials": n_trials,
                    "trials": trials,
                    "llm_pass_rate": n_llm / max(n_trials, 1),
                    "physics_pass_rate": n_phys / max(n_trials, 1),
                    "agreement_rate": n_agree / max(n_trials, 1),
                    "mean_tool_calls": sum(t["n_tool_calls"] for t in trials)
                                       / max(n_trials, 1),
                    "tokens": {"in": in_t, "out": out_t},
                    "cost_usd": _cost_usd(args.model, in_t, out_t),
                })
                result["suites"][suite_name] = {"tasks": suite_results}
                _checkpoint()
            result["suites"][suite_name] = {"tasks": suite_results}
            _checkpoint()

    wall = time.time() - t_start
    result["meta"]["completed_at"] = datetime.utcnow().isoformat()
    result["meta"]["wall_clock_sec"] = wall
    agg = _build_aggregate(result, wall=wall)
    n_total = agg["n_trials_total"]

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved: {args.output}")
    print(f"  trials={n_total}")
    print(f"  llm_pass_rate={result['aggregate']['llm_pass_rate']*100:.0f}%")
    print(f"  physics_pass_rate={result['aggregate']['physics_pass_rate']*100:.0f}%")
    print(f"  cost=${result['aggregate']['total_cost_usd']:.2f}")
    print(f"  wall={wall:.1f}s")


if __name__ == "__main__":
    main()
