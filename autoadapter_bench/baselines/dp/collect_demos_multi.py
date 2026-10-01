#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect multi-task ReachXYZ demos: 100 demos × 6 target directions."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.dp.collect_demos import collect_one_episode
from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv


TRAIN_TARGETS = [
    (+0.05, 0.0, 0.0),
    (-0.05, 0.0, 0.0),
    (0.0, +0.05, 0.0),
    (0.0, -0.05, 0.0),
    (0.0, 0.0, +0.05),
    (0.0, 0.0, -0.05),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--n-per-target", type=int, default=100)
    p.add_argument("--max-steps", type=int, default=100)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    all_obs, all_act, all_target, succs = [], [], [], 0
    total_eps = 0
    for tgt in TRAIN_TARGETS:
        env = ReachXYZEnv(workspace=workspace, target_offset_xyz=tgt,
                           max_steps=args.max_steps)
        print(f"\nCollecting {args.n_per_target} demos for target={tgt}...")
        n_succ = 0
        for ep in range(args.n_per_target):
            obs, act, err = collect_one_episode(env, max_steps=args.max_steps)
            if err < 0.02:
                n_succ += 1
            if obs:
                all_obs.extend(obs)
                all_act.extend(act)
                # Per-step we don't need to log target since it's encoded in obs
            total_eps += 1
            if (ep + 1) % 25 == 0:
                print(f"  ep {ep+1}/{args.n_per_target}  succ_so_far={n_succ}")
        succs += n_succ
        print(f"  → {n_succ}/{args.n_per_target} succeeded")

    obs_arr = np.array(all_obs, dtype=np.float32)
    act_arr = np.array(all_act, dtype=np.float32)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_path, obs=obs_arr, act=act_arr,
              train_targets=np.array(TRAIN_TARGETS, dtype=np.float32))
    print(f"\nSaved {len(all_obs)} (obs, act) pairs from {total_eps} eps "
          f"({succs}/{total_eps} succeeded).")
    print(f"  obs: {obs_arr.shape}  act: {act_arr.shape}")
    print(f"  → {out_path}")


if __name__ == "__main__":
    main()
