#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Franka reach eval — corrected methodology, N=10 episodes per variant.

Methodology note (see paper §6 scoping / canonical.yaml):
  TaskPlanner.execute_task does NOT call skel.home() before running the
  agent — the agent sees a fresh skel at MJCF-default qpos. Targets here
  are computed from a SEPARATE fresh skel (also no home()) so agent input
  and target reference frame match.

This script replaces `llm_franka_proper_reach.json` (n=1) with an n=10
version that also adds 3 new variants (y-, z-, diag).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner

WORKSPACE = REPO_ROOT / "artifacts" / "auto_adapter_from_scratch_franka_artifacts"
DEFAULT_MODEL = os.environ.get(
    "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

# (variant, offset_meters)
REACH_VARIANTS = {
    "reach_x+5":  (+0.05, 0.0, 0.0),
    "reach_x-5":  (-0.05, 0.0, 0.0),
    "reach_y+5":  (0.0, +0.05, 0.0),
    "reach_y-5":  (0.0, -0.05, 0.0),
    "reach_z+5":  (0.0, 0.0, +0.05),
    "reach_z-5":  (0.0, 0.0, -0.05),
    "reach_diag": (+0.035, +0.035, 0.0),   # ~5 cm XY diagonal
}

SUCCESS_TOL_M = 0.02  # 2 cm Cartesian tolerance


def _fresh_ee() -> np.ndarray:
    """Build a fresh Robot from the Franka driver and return its EE position
    WITHOUT calling home(). This matches what the agent sees."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("driver_fr", str(WORKSPACE / "driver.py"))
    mod = importlib.util.module_from_spec(spec)
    orig_cwd = os.getcwd()
    os.chdir(WORKSPACE)
    try:
        spec.loader.exec_module(mod)
        skel = mod.Robot.build_from_mjcf("mjcf.xml")
    finally:
        os.chdir(orig_cwd)
    ee, _ = skel.get_ee_pose()
    return np.array(ee, dtype=float)


def _replay_and_measure(planner: TaskPlanner, tool_log: list) -> np.ndarray:
    """Replay tool calls on a fresh skel (no home()) and return final EE."""
    skel = planner._load_driver()
    for call in tool_log:
        tool = call["tool"]
        inp = call.get("input", {})
        method = getattr(skel, tool, None)
        if method is None:
            continue
        try:
            if tool == "move_cartesian":
                method(np.array([inp["x"], inp["y"], inp["z"]]),
                       duration=float(inp.get("duration", 2.0)))
            elif tool in ("home", "gripper_open", "gripper_close"):
                method()
            else:
                method()
        except Exception as e:  # noqa: BLE001
            print(f"   replay error on {tool}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            break
    ee, _ = skel.get_ee_pose()
    return np.array(ee, dtype=float)


def _build_prompt(variant: str, offset: tuple) -> str:
    if variant == "reach_diag":
        return ("Move the end-effector +3.5 cm in X AND +3.5 cm in Y "
                "simultaneously from its current position (a diagonal "
                "5 cm motion in the XY plane). Use get_ee_pose to read "
                "current position, then compute target.")
    sign = "+" if offset[np.argmax(np.abs(offset))] > 0 else "-"
    axis = "XYZ"[int(np.argmax(np.abs(offset)))]
    return (f"Move the end-effector {sign}5 cm in {axis} from its current "
            f"position. Stop there, do not return. Use get_ee_pose to read "
            f"current position and add the offset.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=10)
    parser.add_argument("--output", default=str(
        REPO_ROOT / "autoadapter_bench" / "baselines" /
        "llm_franka_proper_reach_n10.json"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--variants", default="all",
                        help="comma-separated subset of variants, or 'all'")
    args = parser.parse_args()

    variants = (list(REACH_VARIANTS.keys()) if args.variants == "all"
                else args.variants.split(","))

    planner = TaskPlanner(workspace=WORKSPACE, bedrock_model=args.model)

    results: dict = {}
    t_start = time.time()
    total_cost = 0.0

    for variant in variants:
        offset = REACH_VARIANTS[variant]
        prompt = _build_prompt(variant, offset)
        variant_trials = []

        print(f"\n=== {variant} (offset={offset}) ===")

        # Compute target from a FRESH skel (matches agent's view).
        ee0 = _fresh_ee()
        target = ee0 + np.array(offset)
        print(f"   fresh EE: {ee0.tolist()} → target: {target.tolist()}")

        for trial in range(args.n_trials):
            print(f"   trial {trial+1}/{args.n_trials} ", end="", flush=True)
            t0 = time.time()
            try:
                r = planner.execute_task(prompt,
                                         task_id=f"{variant}_t{trial}",
                                         capture_video=False)
            except Exception as e:  # noqa: BLE001
                print(f"CRASH: {type(e).__name__}: {e}")
                variant_trials.append({
                    "trial": trial, "success": False, "err_m": None,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            dt = time.time() - t0

            # Replay on fresh skel
            try:
                ee_final = _replay_and_measure(planner, r.tool_call_log)
                err = float(np.linalg.norm(ee_final - target))
                success = err < SUCCESS_TOL_M
            except Exception as e:  # noqa: BLE001
                ee_final, err, success = None, None, False
                print(f"REPLAY ERR ({e})", end=" ")

            # Cost approx: $3/M input, $15/M output for Sonnet 4.5
            tu = r.token_usage or {}
            t_in = int(tu.get("in", tu.get("input_tokens", 0)) or 0)
            t_out = int(tu.get("out", tu.get("output_tokens", 0)) or 0)
            cost = t_in * 3e-6 + t_out * 15e-6
            total_cost += cost

            mark = "✓" if success else "✗"
            print(f"{mark} err={err*100:.2f}cm dt={dt:.1f}s cost=${cost:.3f}")

            variant_trials.append({
                "trial": trial,
                "success": bool(success),
                "err_m": float(err) if err is not None else None,
                "duration_sec": float(dt),
                "n_tool_calls": len(r.tool_call_log),
                "tokens": {"in": t_in, "out": t_out},
                "cost_usd": cost,
                "summary": (r.summary or "")[:140],
            })

        n_succ = sum(1 for t in variant_trials if t["success"])
        rate = n_succ / max(1, len(variant_trials))
        results[variant] = {
            "n_success": n_succ,
            "n_total": len(variant_trials),
            "success_rate": rate,
            "mean_err_m": float(np.mean([t["err_m"] for t in variant_trials
                                         if t["err_m"] is not None])
                                if any(t["err_m"] is not None for t in variant_trials)
                                else None),
            "trials": variant_trials,
        }
        print(f"   {variant}: {n_succ}/{len(variant_trials)} = {rate*100:.0f}%")

    wall = time.time() - t_start
    all_rates = [v["success_rate"] for v in results.values()]
    summary = {
        "n_variants": len(results),
        "n_trials_per_variant": args.n_trials,
        "n_trials_total": sum(v["n_total"] for v in results.values()),
        "n_success_total": sum(v["n_success"] for v in results.values()),
        "overall_success_rate": float(np.mean(all_rates)) if all_rates else 0.0,
        "total_cost_usd": total_cost,
        "wall_clock_sec": wall,
    }

    out = {
        "metadata": {
            "workspace": str(WORKSPACE),
            "n_trials_per_variant": args.n_trials,
            "methodology": "fresh skel (no home() call) for both agent and target computation",
            "agent_model": args.model,
            "success_tol_m": SUCCESS_TOL_M,
            "variants": variants,
        },
        "results": results,
        "summary": summary,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {out_path}")
    print(f"Overall: {summary['n_success_total']}/{summary['n_trials_total']}"
          f" = {summary['overall_success_rate']*100:.0f}%  "
          f"(${total_cost:.2f}, {wall/60:.1f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
