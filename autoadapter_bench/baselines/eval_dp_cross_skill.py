#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-skill DP eval matrix: DP_reach on Reach + Pick, DP_pick on Reach + Pick."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddim import DDIMScheduler

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.dp.train_dp import CondConvStack1D
from autoadapter_bench.baselines.dp.eval_dp import predict_action_chunk
from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv
from autoadapter_bench.baselines.rl.pick_env import PickBananaEnv


REACH_TASKS = {
    "reach_x+5": (+0.05, 0.0, 0.0),
    "reach_x-5": (-0.05, 0.0, 0.0),
    "reach_y+5": (0.0, +0.05, 0.0),
    "reach_z+5": (0.0, 0.0, +0.05),
}


def load_dp(path: Path, device: str):
    ckpt = torch.load(str(path), map_location=device, weights_only=False)
    model = CondConvStack1D(
        action_dim=ckpt["action_dim"], state_dim=ckpt["state_dim"]
    ).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, ckpt


def eval_on_reach(model, ckpt, workspace: Path, n_episodes: int,
                    n_exec: int = 8, max_steps: int = 100, device: str = "mps") -> dict:
    sched = DDIMScheduler(num_train_timesteps=100, beta_schedule="squaredcos_cap_v2",
                           clip_sample=True, prediction_type="epsilon")
    out = {}
    for name, offset in REACH_TASKS.items():
        env = ReachXYZEnv(workspace=workspace, target_offset_xyz=offset,
                           with_gripper=True, with_task_id=True, max_steps=max_steps)
        succs, errs = 0, []
        for ep in range(n_episodes):
            obs, _ = env.reset()
            done, n = False, 0
            while not done and n < max_steps:
                chunk = predict_action_chunk(model, ckpt, obs, sched, device=device)
                for k in range(min(n_exec, max_steps - n)):
                    obs, r, term, trunc, info = env.step(chunk[k])
                    n += 1
                    if term or trunc:
                        break
                done = term or trunc
            errs.append(info["err_m"])
            if info["err_m"] < 0.02:
                succs += 1
        out[name] = {"success_rate": succs / n_episodes,
                      "mean_err_m": float(np.mean(errs))}
    return out


def eval_on_pick(model, ckpt, workspace: Path, n_episodes: int,
                  n_exec: int = 8, max_steps: int = 200,
                  ctrl_scale: float = 0.1, device: str = "mps") -> dict:
    sched = DDIMScheduler(num_train_timesteps=100, beta_schedule="squaredcos_cap_v2",
                           clip_sample=True, prediction_type="epsilon")
    env = PickBananaEnv(workspace=workspace, max_steps=max_steps,
                         ctrl_scale=ctrl_scale, with_task_id=True)
    succs, lifts = 0, []
    for ep in range(n_episodes):
        obs, _ = env.reset()
        done, n = False, 0
        while not done and n < max_steps:
            chunk = predict_action_chunk(model, ckpt, obs, sched, device=device)
            for k in range(min(n_exec, max_steps - n)):
                obs, r, term, trunc, info = env.step(chunk[k])
                n += 1
                if term or trunc:
                    break
            done = term or trunc
        lifts.append(info["lift_m"])
        if info["lift_m"] >= 0.02:
            succs += 1
    return {"success_rate": succs / n_episodes,
             "mean_lift_m": float(np.mean(lifts))}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--dp-reach", required=True)
    p.add_argument("--dp-pick",  required=True)
    p.add_argument("--n-episodes", type=int, default=10)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device: {device}")

    workspace = Path(args.workspace).resolve()
    print(f"loading DP_reach from {args.dp_reach}")
    dp_reach, reach_ckpt = load_dp(Path(args.dp_reach), device)
    print(f"loading DP_pick from {args.dp_pick}")
    dp_pick, pick_ckpt = load_dp(Path(args.dp_pick), device)

    results = {}
    print(f"\n=== DP cross-skill matrix (n={args.n_episodes} per cell) ===")
    print(f"{'Policy':<14} {'Reach (avg of 4)':<20} {'Pick':<20}")
    print("-" * 60)

    # DP_reach on each
    rr = eval_on_reach(dp_reach, reach_ckpt, workspace, args.n_episodes, device=device)
    rr_avg = np.mean([v["success_rate"] for v in rr.values()])
    rp = eval_on_pick(dp_reach, reach_ckpt, workspace, args.n_episodes, device=device)
    print(f"{'DP_reach':<14} {rr_avg*100:>5.0f}% (own)        "
          f"{rp['success_rate']*100:>5.0f}% (cross)")

    # DP_pick on each
    pr = eval_on_reach(dp_pick, pick_ckpt, workspace, args.n_episodes, device=device)
    pr_avg = np.mean([v["success_rate"] for v in pr.values()])
    pp = eval_on_pick(dp_pick, pick_ckpt, workspace, args.n_episodes, device=device)
    print(f"{'DP_pick':<14} {pr_avg*100:>5.0f}% (cross)      "
          f"{pp['success_rate']*100:>5.0f}% (own)")

    results = {
        "metadata": {
            "n_episodes_per_cell": args.n_episodes,
            "dp_reach_path": args.dp_reach,
            "dp_pick_path":  args.dp_pick,
            "device": device,
            "n_exec_per_chunk": 8,
        },
        "dp_reach": {"on_reach": rr, "on_pick": rp},
        "dp_pick":  {"on_reach": pr, "on_pick": pp},
    }
    Path(args.output).write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
