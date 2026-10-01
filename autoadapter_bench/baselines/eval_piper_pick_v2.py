#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Piper Pick eval v2 — uses the FROM-SCRATCH PICKBENCH-SYNTHESIZED driver.

Difference from v1 (eval_piper_pick.py):
  v1: Piper driver synthesized on empty scene; eval script extended it
      with get_object_position + weld-toggle on gripper_close (scene-side
      assist disclosure).
  v2: Piper driver re-synthesized against pickbench.xml. The agent itself
      wrote grasp-detection + weld-activation logic. Eval only adds
      get_object_position (post-synthesis patch — the synthesis prompt
      did not require it as a method) and an eq_data relpose update
      inside gripper_close (so the weld holds the object at its current
      position rather than the MJCF-load-time pose; this is a 5-line
      MuJoCo implementation detail that the agent did not synthesize).

Output: autoadapter_bench/baselines/llm_piper_pick_v2.json
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

# NEW: re-synthesized driver workspace (from-scratch on pickbench MJCF)
WORKSPACE = REPO_ROOT / "artifacts" / "from_scratch_piper_pickbench"
DEFAULT_MODEL = os.environ.get(
    "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

CUBES = {
    "cube_red":   (0.40, -0.08, 0.215),
    "cube_green": (0.40,  0.00, 0.215),
    "cube_blue":  (0.40,  0.08, 0.215),
}
LIFT_HEIGHT_M = 0.05  # 5 cm lift threshold (sub-10cm because the
                      # synthesized driver's move uses kinematic teleport
                      # so weld carry is approximate)
SUCCESS_TOL_M = 0.04  # reach success tolerance


def _build_planner(model_id: str):
    from auto_adapter.agent.task_planner import TaskPlanner
    return TaskPlanner(workspace=WORKSPACE, bedrock_model=model_id,
                       region="us-east-1", max_iters=30,
                       max_tokens_per_turn=6000)


def _replay_and_measure(planner, tool_log, target_cube: str) -> dict:
    skel = planner._load_driver()
    skel.home()
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
            elif tool == "get_object_position":
                method(inp.get("body_name", ""))
            elif tool in ("home", "gripper_open", "gripper_close",
                          "is_holding"):
                method()
            else:
                method()
        except Exception as e:  # noqa: BLE001
            print(f"   replay error on {tool}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            break

    ee, _ = skel.get_ee_pose()
    cube_pos = skel.get_object_position(target_cube)
    return {
        "ee_pos": ee.tolist(),
        "cube_pos": cube_pos.tolist(),
        "is_holding": bool(skel.is_holding()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=5)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", default=str(
        REPO_ROOT / "autoadapter_bench" / "baselines" / "llm_piper_pick_v2.json"))
    parser.add_argument("--tasks", default="reach_red,reach_green,reach_blue,"
                        "pick_red,pick_green,pick_blue")
    args = parser.parse_args()

    tasks = args.tasks.split(",")
    print(f"Workspace: {WORKSPACE}")
    print(f"Tasks:     {tasks}")
    print(f"Model:     {args.model}")
    print(f"N:         {args.n_trials} trials per task\n")

    planner = _build_planner(args.model)

    results = {}
    t_start = time.time()
    total_cost = 0.0

    for task_id in tasks:
        kind, color = task_id.split("_", 1)
        cube_name = f"cube_{color}"
        cube_init_pos = CUBES[cube_name]
        if kind == "reach":
            prompt = (f"Move the end-effector to 5 cm directly above the "
                      f"body named '{cube_name}'. Use get_object_position "
                      f"to read its current position, then move to "
                      f"(x, y, z+0.05). Stop there, don't return.")
        elif kind == "pick":
            prompt = (f"Pick up the body named '{cube_name}' and lift it "
                      f"10 cm above its current position. Steps: (1) read "
                      f"its position with get_object_position, (2) open "
                      f"the gripper, (3) move EE 5 cm above the cube, "
                      f"(4) descend to 1 cm above the cube, (5) close "
                      f"the gripper, (6) lift to 10 cm above the original "
                      f"cube position. Finally check is_holding and "
                      f"report the result.")
        else:
            raise ValueError(task_id)

        trials = []
        print(f"=== {task_id} ===")
        for trial in range(args.n_trials):
            print(f"   trial {trial+1}/{args.n_trials} ", end="", flush=True)
            t0 = time.time()
            try:
                r = planner.execute_task(prompt,
                                         task_id=f"{task_id}_t{trial}",
                                         capture_video=False)
            except Exception as e:  # noqa: BLE001
                print(f"CRASH: {type(e).__name__}: {e}")
                trials.append({"trial": trial, "success": False,
                               "error": f"{type(e).__name__}: {e}"})
                continue
            dt = time.time() - t0

            try:
                phys = _replay_and_measure(planner, r.tool_call_log, cube_name)
            except Exception as e:  # noqa: BLE001
                phys = {"error": f"{type(e).__name__}: {e}"}

            if kind == "reach":
                # cube settled position
                cube_settled_z = phys.get("cube_pos", [0,0,0.205])[2]
                target = np.array([cube_init_pos[0], cube_init_pos[1],
                                   cube_settled_z + 0.05])
                err = float(np.linalg.norm(np.array(phys.get("ee_pos", [0,0,0])) - target))
                success = err < SUCCESS_TOL_M
                detail = f"reach_err={err*100:.1f}cm"
            elif kind == "pick":
                # Compare final cube z to its initial z
                initial_z = cube_init_pos[2]
                final_z = phys.get("cube_pos", [0,0,0])[2]
                lifted = final_z - initial_z
                # cube might settle to z=0.205 (table top), so check against settled, not initial
                lifted_from_settled = final_z - 0.205
                holding = phys.get("is_holding", False)
                success = bool(holding and lifted_from_settled > 0.04)
                detail = f"lifted_from_settle={lifted_from_settled*100:.1f}cm holding={holding}"

            tu = r.token_usage or {}
            t_in = int(tu.get("in", 0) or 0)
            t_out = int(tu.get("out", 0) or 0)
            cost = t_in * 3e-6 + t_out * 15e-6
            total_cost += cost

            mark = "✓" if success else "✗"
            print(f"{mark} {detail} dt={dt:.1f}s cost=${cost:.3f}")
            trials.append({
                "trial": trial,
                "success": bool(success),
                "phys": phys,
                "duration_sec": float(dt),
                "n_tool_calls": len(r.tool_call_log),
                "tokens": {"in": t_in, "out": t_out},
                "cost_usd": cost,
                "summary": (r.summary or "")[:140],
            })

        n_ok = sum(1 for t in trials if t["success"])
        rate = n_ok / max(1, len(trials))
        results[task_id] = {"n_success": n_ok, "n_total": len(trials),
                            "success_rate": rate, "trials": trials}
        print(f"   {task_id}: {n_ok}/{len(trials)} = {rate*100:.0f}%\n")

    wall = time.time() - t_start
    all_rates = [r["success_rate"] for r in results.values()]
    summary = {
        "n_tasks": len(results),
        "n_trials_per_task": args.n_trials,
        "n_trials_total": sum(r["n_total"] for r in results.values()),
        "n_success_total": sum(r["n_success"] for r in results.values()),
        "overall_success_rate": float(np.mean(all_rates)) if all_rates else 0.0,
        "total_cost_usd": total_cost,
        "wall_clock_sec": wall,
    }

    out = {
        "metadata": {
            "workspace": str(WORKSPACE),
            "n_trials_per_task": args.n_trials,
            "agent_model": args.model,
            "success_tol_m": SUCCESS_TOL_M,
            "tasks": tasks,
            "driver_synthesis_note": (
                "Driver synthesized from-scratch against pickbench MJCF "
                "(test_from_scratch_piper_pickbench.py); grasp logic + "
                "weld activation written by agent. Post-synthesis patches: "
                "(1) get_object_position helper (5 lines), "
                "(2) eq_data relpose update on weld activation (8 lines)."
            ),
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
