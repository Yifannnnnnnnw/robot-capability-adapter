#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train PPO with multi-task target sampling.

Each episode samples a random target offset from {±X, ±Y, ±Z} × 5 cm.
This is the canonical "multi-task RL" formulation: same task family, no
explicit task ID, just the target_xyz embedded in the observation. The
policy needs to LEARN to read the target from obs and act accordingly.

Output: trained_<robot>_reach_multi.zip + training_log.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import ReachXYZEnv


TRAIN_TARGETS = [
    (+0.05, 0.0, 0.0),
    (-0.05, 0.0, 0.0),
    (0.0, +0.05, 0.0),
    (0.0, -0.05, 0.0),
    (0.0, 0.0, +0.05),
    (0.0, 0.0, -0.05),
]


class ProgressCallback(BaseCallback):
    def __init__(self, log_every: int = 5000) -> None:
        super().__init__()
        self.log_every = log_every
        self.last_log = 0
        self.t0 = time.time()
        self.episode_rewards: list[float] = []
        self.episode_successes: list[bool] = []

    def _on_step(self) -> bool:
        for info, done in zip(self.locals.get("infos", []), self.locals.get("dones", [])):
            if done:
                self.episode_rewards.append(info.get("episode", {}).get("r", 0.0))
                err = info.get("err_m", 1.0)
                self.episode_successes.append(err < 0.02)
        if self.num_timesteps - self.last_log >= self.log_every:
            recent_s = self.episode_successes[-100:]
            recent_r = self.episode_rewards[-100:]
            print(f"  steps={self.num_timesteps:>7d}  episodes={len(self.episode_rewards):>5d}  "
                  f"succ={sum(recent_s)/max(len(recent_s),1)*100:5.1f}%  "
                  f"mean_r={sum(recent_r)/max(len(recent_r),1):+.3f}  "
                  f"elapsed={time.time() - self.t0:.0f}s")
            self.last_log = self.num_timesteps
        return True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--total-timesteps", type=int, default=300_000)
    p.add_argument("--output", required=True)
    p.add_argument("--device", default="auto")
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    env = Monitor(ReachXYZEnv(workspace=workspace,
                                target_pool=TRAIN_TARGETS,
                                with_gripper=True,   # 6-dim action for cross-skill parity
                                max_steps=100))
    print(f"Training multi-task PPO on {len(TRAIN_TARGETS)} target directions:")
    for t in TRAIN_TARGETS:
        print(f"  {t}")
    print(f"  obs space: {env.observation_space}")
    print(f"  act space: {env.action_space}")
    print(f"  timesteps: {args.total_timesteps}")
    print()

    model = PPO("MlpPolicy", env, device=args.device,
                 learning_rate=3e-4, n_steps=2048, batch_size=64, n_epochs=10,
                 gamma=0.99, gae_lambda=0.95, clip_range=0.2, verbose=0)
    cb = ProgressCallback(log_every=10000)
    t0 = time.time()
    model.learn(total_timesteps=args.total_timesteps, callback=cb)
    train_time = time.time() - t0

    model_path = Path(args.output).with_suffix(".zip")
    model.save(str(model_path))
    log = {
        "workspace": str(workspace),
        "train_targets": TRAIN_TARGETS,
        "total_timesteps": args.total_timesteps,
        "train_time_sec": train_time,
        "n_episodes": len(cb.episode_rewards),
        "final_succ_rate_100ep": (sum(cb.episode_successes[-100:]) /
                                   max(len(cb.episode_successes[-100:]), 1)),
        "model_path": str(model_path),
    }
    Path(args.output).with_suffix(".json").write_text(json.dumps(log, indent=2))
    print(f"\nSaved: {model_path}")
    print(f"Final 100-episode train succ: {log['final_succ_rate_100ep']*100:.1f}%  "
          f"wall: {train_time:.0f}s")


if __name__ == "__main__":
    main()
