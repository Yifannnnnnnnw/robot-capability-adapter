#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train PPO on the PickBananaEnv — second skill for cross-skill comparison."""
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

from autoadapter_bench.baselines.rl.pick_env import PickBananaEnv


class PickProgress(BaseCallback):
    def __init__(self, log_every: int = 5000) -> None:
        super().__init__()
        self.log_every = log_every
        self.last_log = 0
        self.t0 = time.time()
        self.episode_rewards: list[float] = []
        self.episode_lifts: list[float] = []
        self.episode_successes: list[bool] = []

    def _on_step(self) -> bool:
        for info, done in zip(self.locals.get("infos", []), self.locals.get("dones", [])):
            if done:
                self.episode_rewards.append(info.get("episode", {}).get("r", 0.0))
                lift = info.get("lift_m", 0.0)
                self.episode_lifts.append(lift)
                self.episode_successes.append(lift >= 0.02)
        if self.num_timesteps - self.last_log >= self.log_every:
            recent_s = self.episode_successes[-50:]
            recent_l = self.episode_lifts[-50:]
            recent_r = self.episode_rewards[-50:]
            print(f"  steps={self.num_timesteps:>7d}  episodes={len(self.episode_rewards):>5d}  "
                  f"succ={sum(recent_s)/max(len(recent_s),1)*100:5.1f}%  "
                  f"lift={sum(recent_l)/max(len(recent_l),1)*100:+5.2f}cm  "
                  f"mean_r={sum(recent_r)/max(len(recent_r),1):+.2f}  "
                  f"elapsed={time.time()-self.t0:.0f}s")
            self.last_log = self.num_timesteps
        return True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--total-timesteps", type=int, default=200_000)
    p.add_argument("--output", required=True)
    p.add_argument("--device", default="auto")
    p.add_argument("--ctrl-scale", type=float, default=0.1)
    p.add_argument("--max-steps", type=int, default=200)
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    env = Monitor(PickBananaEnv(workspace=workspace,
                                  max_steps=args.max_steps,
                                  ctrl_scale=args.ctrl_scale))
    print(f"Training PPO on PickBananaEnv:")
    print(f"  workspace: {workspace}")
    print(f"  obs space: {env.observation_space}")
    print(f"  act space: {env.action_space}")
    print(f"  timesteps: {args.total_timesteps}")
    print()

    model = PPO("MlpPolicy", env, device=args.device,
                 learning_rate=3e-4, n_steps=2048, batch_size=64, n_epochs=10,
                 gamma=0.99, gae_lambda=0.95, clip_range=0.2, verbose=0)
    cb = PickProgress(log_every=5000)
    t0 = time.time()
    model.learn(total_timesteps=args.total_timesteps, callback=cb)
    train_time = time.time() - t0

    model_path = Path(args.output).with_suffix(".zip")
    model.save(str(model_path))
    log = {
        "task": "pick_banana",
        "total_timesteps": args.total_timesteps,
        "train_time_sec": train_time,
        "n_episodes": len(cb.episode_rewards),
        "final_succ_rate_50ep": (sum(cb.episode_successes[-50:]) /
                                  max(len(cb.episode_successes[-50:]), 1)),
        "final_mean_lift_m": (sum(cb.episode_lifts[-50:]) /
                                max(len(cb.episode_lifts[-50:]), 1)),
    }
    Path(args.output).with_suffix(".json").write_text(json.dumps(log, indent=2))
    print(f"\nSaved: {model_path}")
    print(f"Final 50-ep succ: {log['final_succ_rate_50ep']*100:.1f}%  "
          f"mean_lift={log['final_mean_lift_m']*100:+.2f}cm  wall={train_time:.0f}s")


if __name__ == "__main__":
    main()
