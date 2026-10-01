#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-skill evaluation: PPO_reach vs PPO_pick on each other's task.

Demonstrates that a policy trained on one skill does not transfer to a
different skill family, even when the env observation/action structure
appears similar. This contrasts with the LLM agent, which uses the same
auto-synthesized skill library for any task description.

Action-space note: ReachXYZEnv has 5-dim actions (joint deltas only);
PickBananaEnv has 6-dim (5 joints + gripper). To cross-evaluate we pad
or truncate as documented in each branch.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv
from autoadapter_bench.baselines.rl.pick_env import PickBananaEnv


REACH_TASKS = {
    "reach_x+5":  (+0.05, 0.0, 0.0),
    "reach_x-5":  (-0.05, 0.0, 0.0),
    "reach_y+5":  (0.0, +0.05, 0.0),
    "reach_z+5":  (0.0, 0.0, +0.05),
}


def run_policy_on_reach(model, workspace: Path, n_episodes: int = 10,
                          max_steps: int = 100) -> dict:
    """Run a PPO model on each reach variant.

    Reach env is built with `with_gripper=True` so its action space matches
    PickEnv's 6 dim — no padding or truncation needed.
    """
    out = {}
    for name, offset in REACH_TASKS.items():
        env = ReachXYZEnv(workspace=workspace, target_offset_xyz=offset,
                           max_steps=max_steps, with_gripper=True)
        succs, errs = 0, []
        for ep in range(n_episodes):
            obs, _ = env.reset()
            done, n_step = False, 0
            while not done and n_step < max_steps:
                a, _ = model.predict(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(a)
                n_step += 1
                done = term or trunc
            errs.append(info["err_m"])
            if info["err_m"] < 0.02:
                succs += 1
        out[name] = {"success_rate": succs / n_episodes,
                      "mean_err_m": float(np.mean(errs))}
    return out


def run_policy_on_pick(model, workspace: Path, n_episodes: int = 10,
                        max_steps: int = 200, ctrl_scale: float = 0.1) -> dict:
    """Run a PPO model on pick. Banana is randomized via env's default."""
    env = PickBananaEnv(workspace=workspace, max_steps=max_steps, ctrl_scale=ctrl_scale)
    succs, lifts = 0, []
    for ep in range(n_episodes):
        obs, _ = env.reset()
        done, n_step = False, 0
        while not done and n_step < max_steps:
            a, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(a)
            n_step += 1
            done = term or trunc
        lifts.append(info["lift_m"])
        if info["lift_m"] >= 0.02:
            succs += 1
    return {"success_rate": succs / n_episodes,
             "mean_lift_m": float(np.mean(lifts))}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--ppo-reach", required=True)
    p.add_argument("--ppo-pick", required=True)
    p.add_argument("--n-episodes", type=int, default=10)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    from stable_baselines3 import PPO
    workspace = Path(args.workspace).resolve()

    print("loading PPO_reach (multi-task)...")
    ppo_reach = PPO.load(args.ppo_reach, device="auto")
    print("loading PPO_pick...")
    ppo_pick = PPO.load(args.ppo_pick, device="auto")

    print("\n=== Cross-skill matrix ===")
    print(f"{'Policy':<14} {'Reach (4 vars)':<20} {'Pick (1 task)':<20}")
    print("-" * 60)
    results = {}

    # PPO_reach on reach (sanity check — should work)
    reach_on_reach = run_policy_on_reach(ppo_reach, workspace,
                                          n_episodes=args.n_episodes)
    reach_on_reach_avg = np.mean([v["success_rate"] for v in reach_on_reach.values()])
    reach_on_pick = run_policy_on_pick(ppo_reach, workspace,
                                         n_episodes=args.n_episodes)
    print(f"{'PPO_reach':<14} {reach_on_reach_avg*100:>5.0f}% (own task)    "
          f"{reach_on_pick['success_rate']*100:>5.0f}% (cross)")
    pick_on_reach = run_policy_on_reach(ppo_pick, workspace,
                                          n_episodes=args.n_episodes)
    pick_on_reach_avg = np.mean([v["success_rate"] for v in pick_on_reach.values()])
    pick_on_pick = run_policy_on_pick(ppo_pick, workspace,
                                        n_episodes=args.n_episodes)
    print(f"{'PPO_pick':<14} {pick_on_reach_avg*100:>5.0f}% (cross)        "
          f"{pick_on_pick['success_rate']*100:>5.0f}% (own task)")

    results["ppo_reach_on_reach"] = reach_on_reach
    results["ppo_reach_on_pick"] = reach_on_pick
    results["ppo_pick_on_reach"] = pick_on_reach
    results["ppo_pick_on_pick"] = pick_on_pick

    # Emit metadata alongside per-cell results so reviewers can audit n
    output_payload = {
        "metadata": {
            "n_episodes_per_reach_variant": args.n_episodes,
            "n_episodes_per_pick": args.n_episodes,
            "deterministic": True,
            "reach_variants_count": 4,
            "pick_ctrl_scale": 0.1,
            "ppo_reach_model": args.ppo_reach,
            "ppo_pick_model": args.ppo_pick,
            "workspace": str(workspace),
        },
        "results": results,
    }
    Path(args.output).write_text(json.dumps(output_payload, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
