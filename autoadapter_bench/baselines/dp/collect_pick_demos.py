#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect pick-banana demonstrations using scripted IK-based policy.

Each demo: reset env (banana XY randomized) → approach banana → close gripper
→ lift. Record (state, action) pairs in the same 6-dim joint-delta+gripper
action space PPO uses.

Output: demos_pick_random.npz with obs/act arrays and metadata.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.pick_env import PickBananaEnv


def collect_one_pick_episode(env: PickBananaEnv,
                              approach_height: float = 0.03,
                              lift_height: float = 0.10,
                              gripper_close_steps: int = 4,
                              max_steps: int = 200) -> tuple[list, list, float]:
    """Scripted policy:
      Phase 1: IK toward (banana_x, banana_y, banana_z + approach_height)
      Phase 2: close gripper (hold position for gripper_close_steps env steps)
      Phase 3: IK toward (banana_x, banana_y, banana_z + lift_height)
    Episode ends when the BANANA (not the EE) has lifted ≥ env.success_lift_m.
    """
    obs, _ = env.reset()
    skel = env.skel

    banana_xyz = env._get_banana_pos()
    approach_target = banana_xyz.copy()
    approach_target[2] += approach_height
    lift_target = banana_xyz.copy()
    lift_target[2] += lift_height

    obs_log, action_log = [], []
    phase = 1   # 1=approach, 2=close, 3=lift, 4=done
    steps_in_phase = 0
    success_threshold = env.success_lift_m

    for step in range(max_steps):
        cur_q = skel.get_joint_positions()
        ee_xyz, _ = skel.get_ee_pose()
        banana_now = env._get_banana_pos()
        actual_lift = float(banana_now[2] - env._banana_start_z)

        # Phase transitions
        if phase == 1 and float(np.linalg.norm(approach_target - ee_xyz)) < 0.02:
            phase = 2
            steps_in_phase = 0
        elif phase == 2 and steps_in_phase >= gripper_close_steps:
            phase = 3
            steps_in_phase = 0
        elif phase == 3 and actual_lift >= success_threshold:
            phase = 4   # success — banana actually lifted ≥ tol

        if phase == 4:
            break

        # Compute desired EE based on phase. We solve IK to the FULL goal
        # (not a sub-target), then let env.step apply a single clipped joint
        # delta toward it. This is the same approach used for reach demos.
        if phase == 1:
            try:
                q_des = skel.ik(approach_target, q_init=cur_q)
            except Exception:
                break
            gripper_cmd = -1.0
        elif phase == 2:
            q_des = cur_q   # stay still while gripper closes
            gripper_cmd = +1.0
        else:   # phase 3
            try:
                q_des = skel.ik(lift_target, q_init=cur_q)
            except Exception:
                break
            gripper_cmd = +1.0

        # Convert q_des → joint-delta action (normalized to [-1, 1])
        delta = (q_des - cur_q) / env.ctrl_scale
        joint_action = np.clip(delta, -1.0, 1.0).astype(np.float32)
        action = np.concatenate([joint_action, [gripper_cmd]]).astype(np.float32)

        obs_log.append(obs.astype(np.float32))
        action_log.append(action)
        obs, r, term, trunc, info = env.step(action)
        steps_in_phase += 1
        if term or trunc:
            break

    final_lift = float(env._get_banana_pos()[2] - env._banana_start_z)
    return obs_log, action_log, final_lift


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--n-episodes", type=int, default=200)
    p.add_argument("--ctrl-scale", type=float, default=0.1)
    p.add_argument("--max-steps", type=int, default=200)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    env = PickBananaEnv(workspace=workspace,
                         max_steps=args.max_steps,
                         ctrl_scale=args.ctrl_scale,
                         with_task_id=True)

    all_obs, all_act, succs = [], [], 0
    for ep in range(args.n_episodes):
        obs, act, lift = collect_one_pick_episode(env, max_steps=args.max_steps)
        if lift >= 0.02:
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
              n_episodes=args.n_episodes, n_succeeded=succs,
              ctrl_scale=args.ctrl_scale, max_steps=args.max_steps)
    print(f"\nSaved {len(all_obs)} (obs, action) pairs from {args.n_episodes} eps "
          f"({succs} succeeded).")
    print(f"  obs: {obs_arr.shape}  act: {act_arr.shape}")
    print(f"  → {out_path}")


if __name__ == "__main__":
    main()
