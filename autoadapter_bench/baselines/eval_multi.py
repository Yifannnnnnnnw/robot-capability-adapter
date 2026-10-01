#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evaluate multi-task PPO + multi-task DP on a fixed task grid.

Grid (10 variants):
  In-distribution (trained on these 6 directions):
    +X, -X, +Y, -Y, +Z, -Z  (all 5 cm magnitude)
  Out-of-distribution (held out):
    diag (+3,+3,+3)              — multi-axis combination
    magnitude (+10, 0, 0)         — direction seen, magnitude novel
    mixed (+5, +5, 0)             — two-axis combination
    far_diag (4, 4, -2)           — non-grid with negative Z
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


TASKS = {
    # 6 in-distribution directions
    "in_x+5":      (+0.05, 0.0, 0.0),
    "in_x-5":      (-0.05, 0.0, 0.0),
    "in_y+5":      (0.0, +0.05, 0.0),
    "in_y-5":      (0.0, -0.05, 0.0),
    "in_z+5":      (0.0, 0.0, +0.05),
    "in_z-5":      (0.0, 0.0, -0.05),
    # 4 OOD held-out
    "ood_diag":     (+0.03, +0.03, +0.03),
    "ood_mag":      (+0.10, 0.0, 0.0),
    "ood_mixed":    (+0.05, +0.05, 0.0),
    "ood_far_diag": (+0.04, +0.04, -0.02),
}
IN_DIST_KEYS = [k for k in TASKS if k.startswith("in_")]
OOD_KEYS = [k for k in TASKS if k.startswith("ood_")]


# ──────────────────────────────────────────────────────────────────────────
# PPO eval
# ──────────────────────────────────────────────────────────────────────────


def eval_ppo(model_path: Path, workspace: Path, n_episodes: int = 10,
              max_steps: int = 100) -> dict:
    from stable_baselines3 import PPO
    model = PPO.load(str(model_path), device="auto")
    out = {}
    print(f"\n=== PPO eval ({model_path.name}) ===")
    print(f"{'Task':<14} {'success':>8} {'err':>8} {'steps':>7}")
    print("-" * 45)
    for name, offset in TASKS.items():
        env = ReachXYZEnv(workspace=workspace, target_offset_xyz=offset,
                           max_steps=max_steps)
        succs, errs, all_steps = 0, [], 0
        for ep in range(n_episodes):
            obs, _ = env.reset()
            done, n_step = False, 0
            while not done and n_step < max_steps:
                action, _ = model.predict(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(action)
                n_step += 1
                done = term or trunc
            errs.append(info["err_m"])
            all_steps += n_step
            if info["err_m"] < 0.02:
                succs += 1
        out[name] = {
            "success_rate": succs / n_episodes,
            "mean_err_m": float(np.mean(errs)),
            "mean_steps": all_steps / n_episodes,
            "target_offset": list(offset),
        }
        print(f"{name:<14} {out[name]['success_rate']*100:>7.1f}% "
              f"{out[name]['mean_err_m']*100:>6.2f}cm {out[name]['mean_steps']:>7.1f}")
    return out


# ──────────────────────────────────────────────────────────────────────────
# DP eval
# ──────────────────────────────────────────────────────────────────────────


def eval_dp(model_path: Path, workspace: Path, n_episodes: int = 10,
             max_steps: int = 100, n_exec: int = 8) -> dict:
    from diffusers.schedulers.scheduling_ddim import DDIMScheduler
    from autoadapter_bench.baselines.dp.train_dp import CondConvStack1D
    from autoadapter_bench.baselines.dp.eval_dp import predict_action_chunk

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    ckpt = torch.load(str(model_path), map_location=device, weights_only=False)
    model = CondConvStack1D(
        action_dim=ckpt["action_dim"], state_dim=ckpt["state_dim"]
    ).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    sched = DDIMScheduler(num_train_timesteps=100,
                           beta_schedule="squaredcos_cap_v2",
                           clip_sample=True, prediction_type="epsilon")

    out = {}
    print(f"\n=== DP eval ({model_path.name}) ===")
    print(f"{'Task':<14} {'success':>8} {'err':>8} {'steps':>7}")
    print("-" * 45)
    for name, offset in TASKS.items():
        env = ReachXYZEnv(workspace=workspace, target_offset_xyz=offset,
                           max_steps=max_steps)
        succs, errs, all_steps = 0, [], 0
        for ep in range(n_episodes):
            obs, _ = env.reset()
            n_step = 0
            term = trunc = False
            while not (term or trunc) and n_step < max_steps:
                chunk = predict_action_chunk(model, ckpt, obs, sched, device=device)
                for k in range(min(n_exec, max_steps - n_step)):
                    obs, r, term, trunc, info = env.step(chunk[k])
                    n_step += 1
                    if term or trunc:
                        break
            errs.append(info["err_m"])
            all_steps += n_step
            if info["err_m"] < 0.02:
                succs += 1
        out[name] = {
            "success_rate": succs / n_episodes,
            "mean_err_m": float(np.mean(errs)),
            "mean_steps": all_steps / n_episodes,
            "target_offset": list(offset),
        }
        print(f"{name:<14} {out[name]['success_rate']*100:>7.1f}% "
              f"{out[name]['mean_err_m']*100:>6.2f}cm {out[name]['mean_steps']:>7.1f}")
    return out


# ──────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────


def summarize(results: dict, label: str) -> None:
    in_dist_succ = np.mean([results[k]["success_rate"] for k in IN_DIST_KEYS])
    ood_succ = np.mean([results[k]["success_rate"] for k in OOD_KEYS])
    print(f"\n{label}:")
    print(f"  In-distribution avg success: {in_dist_succ*100:.0f}% "
          f"({len(IN_DIST_KEYS)} variants)")
    print(f"  OOD avg success:             {ood_succ*100:.0f}% "
          f"({len(OOD_KEYS)} variants)")
    print(f"  Generalization gap:           {(in_dist_succ-ood_succ)*100:.0f}pp")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--ppo-model", default=None)
    p.add_argument("--dp-model", default=None)
    p.add_argument("--n-episodes", type=int, default=10)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    all_results = {}

    if args.ppo_model:
        all_results["ppo_multi"] = eval_ppo(Path(args.ppo_model), workspace,
                                             n_episodes=args.n_episodes)
        summarize(all_results["ppo_multi"], "Multi-task PPO")
    if args.dp_model:
        all_results["dp_multi"] = eval_dp(Path(args.dp_model), workspace,
                                           n_episodes=args.n_episodes)
        summarize(all_results["dp_multi"], "Multi-task DP")

    Path(args.output).write_text(json.dumps({
        "in_dist_keys": IN_DIST_KEYS,
        "ood_keys": OOD_KEYS,
        "results": all_results,
    }, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
