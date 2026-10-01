#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evaluate trained Diffusion Policy on the 5 reach-task variants.

Same protocol as the PPO eval: 10 episodes per variant, success = final EE
within 2 cm of target. Tests generalization across reach directions when the
DP was trained only on +X 5 cm demos.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddim import DDIMScheduler

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.dp.train_dp import CondConvStack1D
from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv


def load_dp(model_path: Path, device: str = "mps") -> tuple:
    ckpt = torch.load(str(model_path), map_location=device, weights_only=False)
    model = CondConvStack1D(
        action_dim=ckpt["action_dim"], state_dim=ckpt["state_dim"]
    ).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, ckpt


def predict_action_chunk(model, ckpt, state: np.ndarray,
                         scheduler: DDIMScheduler, n_steps: int = 10,
                         device: str = "mps") -> np.ndarray:
    """Run DDIM inference to denoise an action chunk conditioned on state."""
    state_t = torch.from_numpy(state).float().to(device).unsqueeze(0)  # [1, S]
    state_n = (state_t - ckpt["obs_mean"].to(device)) / ckpt["obs_std"].to(device)
    action_dim = ckpt["action_dim"]
    horizon = ckpt["horizon"]
    # Start from pure noise
    x = torch.randn(1, action_dim, horizon, device=device)
    scheduler.set_timesteps(n_steps)
    with torch.no_grad():
        for t in scheduler.timesteps:
            t_b = torch.tensor([t.item()], device=device).long()
            noise_pred = model(x, t_b, state_n)
            x = scheduler.step(noise_pred, t, x).prev_sample
    x = x.squeeze(0).permute(1, 0).cpu().numpy()  # [T, action_dim]
    # Denormalize (mean/std might be on any device — pull to CPU numpy)
    x = x * ckpt["act_std"].cpu().numpy() + ckpt["act_mean"].cpu().numpy()
    return x


def eval_dp_on_task(model, ckpt, workspace: Path, target_offset,
                     n_episodes: int = 10, max_steps: int = 100,
                     n_exec_per_chunk: int = 8,
                     device: str = "mps") -> dict:
    env = ReachXYZEnv(workspace=workspace, target_offset_xyz=target_offset,
                       max_steps=max_steps)
    scheduler = DDIMScheduler(
        num_train_timesteps=100,
        beta_schedule="squaredcos_cap_v2",
        clip_sample=True,
        prediction_type="epsilon",
    )
    succs = 0
    final_errs = []
    total_steps = 0
    for ep in range(n_episodes):
        obs, _ = env.reset()
        for step in range(0, max_steps, n_exec_per_chunk):
            chunk = predict_action_chunk(model, ckpt, obs, scheduler, device=device)
            for k in range(min(n_exec_per_chunk, max_steps - step)):
                obs, r, term, trunc, info = env.step(chunk[k])
                total_steps += 1
                if term or trunc:
                    break
            if term or trunc:
                break
        final_errs.append(float(info["err_m"]))
        if info["err_m"] < 0.02:
            succs += 1
    return {
        "n_episodes": n_episodes,
        "success_rate": succs / n_episodes,
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
    p.add_argument("--device", default="auto")
    args = p.parse_args()

    device = ("mps" if torch.backends.mps.is_available() else "cpu") \
        if args.device == "auto" else args.device
    print(f"using device: {device}")

    workspace = Path(args.workspace).resolve()
    model, ckpt = load_dp(Path(args.model), device=device)

    tasks = {
        "in_dist_x+5":   (+0.05, 0.0, 0.0),
        "ood_y+5":       (0.0, +0.05, 0.0),
        "ood_z+5":       (0.0, 0.0, +0.05),
        "ood_diag":      (+0.03, +0.03, +0.03),
        "ood_neg_x":     (-0.05, 0.0, 0.0),
    }
    results = {}
    print(f"\nEvaluating DP on {len(tasks)} task variants ({args.n_episodes} episodes each):")
    print(f"{'Task':<14} {'success':>8} {'mean_err':>10} {'mean_steps':>11}")
    print("-" * 50)
    for name, offset in tasks.items():
        t0 = time.time()
        r = eval_dp_on_task(model, ckpt, workspace, offset,
                             n_episodes=args.n_episodes, device=device)
        r["eval_time_sec"] = time.time() - t0
        results[name] = r
        print(f"{name:<14} {r['success_rate']*100:>7.1f}% "
               f"{r['mean_final_err_m']*100:>9.2f}cm {r['mean_steps_per_episode']:>10.1f}")
    print()
    in_dist = results["in_dist_x+5"]["success_rate"]
    ood = [v["success_rate"] for k, v in results.items() if k != "in_dist_x+5"]
    ood_avg = sum(ood) / max(len(ood), 1)
    print(f"  In-distribution: {in_dist*100:.0f}%")
    print(f"  OOD avg:         {ood_avg*100:.0f}%")
    print(f"  Generalization gap: {(in_dist - ood_avg)*100:.0f}pp")

    Path(args.output).write_text(json.dumps({
        "model_path": args.model,
        "n_episodes_per_task": args.n_episodes,
        "in_distribution_success_rate": in_dist,
        "ood_avg_success_rate": ood_avg,
        "generalization_gap_pp": (in_dist - ood_avg) * 100,
        "per_task": results,
    }, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
