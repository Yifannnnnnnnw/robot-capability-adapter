#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect (obs, action) demonstrations for one ReachXYZ task.

Demos come from the skel's IK (via `move_cartesian`). We log the joint-delta
action taken at each sim sub-step so the demos are in the SAME action space
as the PPO/DP policy (joint-delta scaled by ctrl_scale).

This is the apple-to-apple control space for Diffusion Policy vs PPO.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv


def collect_one_episode(env: ReachXYZEnv, max_steps: int = 100,
                          stop_tol: float = 0.005) -> tuple[list, list, float]:
    """Use the skel's IK to drive EE toward target, collect joint-delta actions."""
    obs, _ = env.reset()
    skel = env.skel
    target_xyz = env._target_xyz.copy()

    obs_log, action_log = [], []
    for step in range(max_steps):
        # Get current EE and current q
        ee_xyz, _ = skel.get_ee_pose()
        cur_q = skel.get_joint_positions()
        err = target_xyz - ee_xyz
        err_norm = float(np.linalg.norm(err))
        if err_norm < stop_tol:
            break

        # Use IK to compute joint targets for a sub-target one step toward goal.
        step_size = min(0.01, err_norm)
        sub_target = ee_xyz + err / max(err_norm, 1e-6) * step_size
        try:
            q_des = skel.ik(sub_target, q_init=cur_q)
        except Exception:
            # Fall back: nudge cartesian and read resulting q
            skel.move_cartesian(ee_xyz + err * 0.1, duration=0.05)
            q_des = skel.get_joint_positions()

        # Compute the joint-delta action equivalent (normalized to [-1, 1])
        delta = (q_des - cur_q) / env.ctrl_scale
        delta = np.clip(delta, -1.0, 1.0).astype(np.float32)

        # Step the env with this action
        obs_log.append(obs.astype(np.float32))
        action_log.append(delta)
        obs, r, term, trunc, info = env.step(delta)
        if term or trunc:
            break
    final_err = float(np.linalg.norm(target_xyz - skel.get_ee_pose()[0]))
    return obs_log, action_log, final_err


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--n-episodes", type=int, default=200)
    p.add_argument("--max-steps", type=int, default=100)
    p.add_argument("--output", required=True)
    p.add_argument("--target-offset", nargs=3, type=float, default=[0.05, 0.0, 0.0],
                   help="Reach target offset in (x, y, z)")
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    env = ReachXYZEnv(workspace=workspace,
                       target_offset_xyz=tuple(args.target_offset),
                       max_steps=args.max_steps)

    all_obs, all_act, succs = [], [], 0
    for ep in range(args.n_episodes):
        obs, act, err = collect_one_episode(env, max_steps=args.max_steps)
        if err < 0.02:
            succs += 1
        if obs:
            all_obs.extend(obs)
            all_act.extend(act)
        if (ep + 1) % 20 == 0:
            print(f"  episode {ep+1}/{args.n_episodes}  steps_total={len(all_obs)}  "
                  f"succ_so_far={succs}/{ep+1}")

    obs_arr = np.array(all_obs, dtype=np.float32)
    act_arr = np.array(all_act, dtype=np.float32)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_path, obs=obs_arr, act=act_arr,
              target_offset=np.array(args.target_offset, dtype=np.float32))
    print(f"\nSaved {len(all_obs)} (obs, action) pairs from {args.n_episodes} eps "
          f"({succs} succeeded).")
    print(f"  obs: {obs_arr.shape}  act: {act_arr.shape}")
    print(f"  → {out_path}")


if __name__ == "__main__":
    main()
