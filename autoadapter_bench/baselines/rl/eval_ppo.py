#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evaluate trained PPO on (a) the SAME task it trained on, (b) related but
DIFFERENT tasks, to show the generalization gap. Compare to our LLM agent
on the same tasks.

The point for the paper: PPO learns a fixed policy. Our LLM agent generalizes
to ANY new task description with the same per-task LLM cost.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from stable_baselines3 import PPO

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv


def eval_policy_on_task(model, workspace: Path,
                         target_offset, n_episodes: int = 10,
                         max_steps: int = 100) -> dict:
    env = ReachXYZEnv(workspace=workspace, target_offset_xyz=target_offset,
                       max_steps=max_steps)
    successes = 0
    final_errs = []
    total_steps = 0
    for ep in range(n_episodes):
        obs, _ = env.reset()
        done = False
        n_step = 0
        while not done and n_step < max_steps:
            action, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(action)
            n_step += 1
            done = term or trunc
        final_errs.append(float(info["err_m"]))
        total_steps += n_step
        if info["err_m"] < 0.02:
            successes += 1
    return {
        "n_episodes": n_episodes,
        "success_rate": successes / n_episodes,
        "mean_final_err_m": sum(final_errs) / len(final_errs),
        "mean_steps_per_episode": total_steps / n_episodes,
        "target_offset": list(target_offset),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--n-episodes", type=int, default=10)
    args = p.parse_args()

    print(f"Loading PPO from {args.model}")
    model = PPO.load(args.model, device="auto")

    workspace = Path(args.workspace).resolve()

    # Generalization probe: 4 different reach targets
    # - same as training (+5cm X)
    # - 3 different offsets (Y, Z, diagonal)
    tasks = {
        "in_dist_x+5":   (+0.05, 0.0, 0.0),    # train distribution
        "ood_y+5":       (0.0, +0.05, 0.0),    # different axis
        "ood_z+5":       (0.0, 0.0, +0.05),    # vertical
        "ood_diag":      (+0.03, +0.03, +0.03), # combined
        "ood_neg_x":     (-0.05, 0.0, 0.0),    # opposite direction
    }
    results = {}
    print(f"\nEvaluating on {len(tasks)} task variants ({args.n_episodes} episodes each):")
    print(f"{'Task':<14} {'success':>8} {'mean_err':>10} {'mean_steps':>11}")
    print("-" * 50)
    for name, offset in tasks.items():
        t0 = time.time()
        r = eval_policy_on_task(model, workspace, offset, n_episodes=args.n_episodes)
        r["eval_time_sec"] = time.time() - t0
        results[name] = r
        print(f"{name:<14} {r['success_rate']*100:>7.1f}% {r['mean_final_err_m']*100:>9.2f}cm {r['mean_steps_per_episode']:>10.1f}")
    print()
    in_dist = results["in_dist_x+5"]["success_rate"]
    ood_means = [v["success_rate"] for k, v in results.items() if k != "in_dist_x+5"]
    ood_avg = sum(ood_means) / max(len(ood_means), 1)
    print(f"  In-distribution (trained-on) success: {in_dist*100:.0f}%")
    print(f"  Out-of-distribution avg success:      {ood_avg*100:.0f}%")
    print(f"  Generalization gap: {(in_dist - ood_avg)*100:.0f}pp")

    Path(args.output).write_text(json.dumps({
        "model_path": args.model,
        "workspace": str(workspace),
        "n_episodes_per_task": args.n_episodes,
        "in_distribution_success_rate": in_dist,
        "ood_avg_success_rate": ood_avg,
        "generalization_gap_pp": (in_dist - ood_avg) * 100,
        "per_task": results,
    }, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
