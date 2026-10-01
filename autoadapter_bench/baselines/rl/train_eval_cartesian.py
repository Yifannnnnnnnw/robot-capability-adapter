#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train + cross-evaluate the same-API learned baselines (cartesian_env.py).

Produces the apples-to-apples cross-skill row Codex asked for: a learned
policy on the SAME Cartesian+gripper tool layer the LLM/CaP use, evaluated
Reach<->Pick on the same episodes.

    # train both
    python -m autoadapter_bench.baselines.rl.train_eval_cartesian \
        --workspace artifacts/auto_adapter_so101_v2_artifacts --mode train_reach
    ... --mode train_pick
    # cross-eval matrix
    ... --mode crosseval --output autoadapter_bench/baselines/cartesian_cross_skill.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO

from autoadapter_bench.baselines.rl.cartesian_env import (
    CartesianReachEnv, CartesianPickEnv, MultiTaskCartesianEnv)

TRAINED = Path(__file__).resolve().parent / "trained"
REACH_POOL = [(+0.05, 0, 0), (-0.05, 0, 0), (0, +0.05, 0),
              (0, 0, +0.05), (0, 0, -0.05), (0, -0.05, 0)]
REACH_EVAL = {"reach_x+5": (+0.05, 0, 0), "reach_x-5": (-0.05, 0, 0),
              "reach_y+5": (0, +0.05, 0), "reach_z+5": (0, 0, +0.05)}


def _suffix(seed: int) -> str:
    return f"_s{seed}" if seed is not None else ""


def train_reach(ws: Path, steps: int, seed: int = 0) -> Path:
    env = CartesianReachEnv(ws, target_pool=REACH_POOL, max_steps=60, seed=seed)
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=3e-4, n_steps=2048,
                batch_size=256, gamma=0.99, seed=seed, device="cpu")
    t0 = time.time()
    model.learn(total_timesteps=steps)
    TRAINED.mkdir(exist_ok=True)
    out = TRAINED / f"so101_cart_reach{_suffix(seed)}.zip"
    model.save(out)
    print(f"saved {out} ({time.time()-t0:.0f}s)")
    return out


def train_pick(ws: Path, steps: int, seed: int = 0) -> Path:
    env = CartesianPickEnv(ws, max_steps=80, seed=seed)
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=3e-4, n_steps=2048,
                batch_size=256, gamma=0.99, seed=seed, device="cpu")
    t0 = time.time()
    model.learn(total_timesteps=steps)
    TRAINED.mkdir(exist_ok=True)
    out = TRAINED / f"so101_cart_pick{_suffix(seed)}.zip"
    model.save(out)
    print(f"saved {out} ({time.time()-t0:.0f}s)")
    return out


def train_multitask(ws: Path, steps: int, seed: int = 0) -> Path:
    env = MultiTaskCartesianEnv(ws, max_steps=80, p_pick=0.5, seed=seed)
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=3e-4, n_steps=2048,
                batch_size=256, gamma=0.99, seed=seed, device="cpu")
    t0 = time.time()
    model.learn(total_timesteps=steps)
    TRAINED.mkdir(exist_ok=True)
    out = TRAINED / f"so101_cart_multitask{_suffix(seed)}.zip"
    model.save(out)
    print(f"saved {out} ({time.time()-t0:.0f}s)")
    return out


def eval_multitask(model, ws: Path, n_ep: int) -> dict:
    """One multi-task policy, pinned to each skill via force_task."""
    out = {}
    # reach: 4 fixed variants (deterministic), report per-variant
    reach = {}
    for name, off in REACH_EVAL.items():
        env = MultiTaskCartesianEnv(ws, max_steps=80, force_task="reach",
                                    reach_pool=[off], seed=123)
        succ, errs = 0, []
        for _ in range(n_ep):
            obs, _ = env.reset(); done = False
            while not done:
                a, _ = model.predict(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(a); done = term or trunc
            errs.append(info["err_m"]); succ += int(info["err_m"] < 0.02)
        reach[name] = {"success_rate": succ / n_ep, "mean_err_m": float(np.mean(errs))}
    # pick: randomized banana
    penv = MultiTaskCartesianEnv(ws, max_steps=80, force_task="pick", seed=123)
    psucc, lifts = 0, []
    for _ in range(n_ep):
        obs, _ = penv.reset(); done = False
        while not done:
            a, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = penv.step(a); done = term or trunc
        lifts.append(info["lift_m"]); psucc += int(info["lift_m"] >= 0.02)
    out["multitask_on_reach"] = reach
    out["multitask_on_reach_avg"] = float(np.mean([v["success_rate"] for v in reach.values()]))
    out["multitask_on_pick"] = {"success_rate": psucc / n_ep, "mean_lift_m": float(np.mean(lifts))}
    return out


def eval_on_reach(model, ws: Path, n_ep: int) -> dict:
    out = {}
    for name, off in REACH_EVAL.items():
        env = CartesianReachEnv(ws, target_offset_xyz=off, max_steps=60, seed=123)
        succ, errs = 0, []
        for ep in range(n_ep):
            obs, _ = env.reset()
            done = False
            while not done:
                a, _ = model.predict(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(a)
                done = term or trunc
            errs.append(info["err_m"])
            succ += int(info["err_m"] < 0.02)
        out[name] = {"success_rate": succ / n_ep, "mean_err_m": float(np.mean(errs))}
    return out


def eval_on_pick(model, ws: Path, n_ep: int) -> dict:
    env = CartesianPickEnv(ws, max_steps=80, seed=123)
    succ, lifts = 0, []
    for ep in range(n_ep):
        obs, _ = env.reset()
        done = False
        while not done:
            a, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(a)
            done = term or trunc
        lifts.append(info["lift_m"])
        succ += int(info["lift_m"] >= 0.02)
    return {"success_rate": succ / n_ep, "mean_lift_m": float(np.mean(lifts))}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--mode", required=True,
                   choices=["train_reach", "train_pick", "train_multitask",
                            "crosseval", "eval_multitask"])
    p.add_argument("--mt-steps", type=int, default=500_000)
    p.add_argument("--reach-steps", type=int, default=250_000)
    p.add_argument("--pick-steps", type=int, default=300_000)
    p.add_argument("--n-episodes", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--output", default="autoadapter_bench/baselines/cartesian_cross_skill.json")
    args = p.parse_args()
    ws = Path(args.workspace).resolve()

    if args.mode == "train_reach":
        train_reach(ws, args.reach_steps, seed=args.seed)
    elif args.mode == "train_pick":
        train_pick(ws, args.pick_steps, seed=args.seed)
    elif args.mode == "train_multitask":
        train_multitask(ws, args.mt_steps, seed=args.seed)
    elif args.mode == "eval_multitask":
        mt = PPO.load(TRAINED / f"so101_cart_multitask{_suffix(args.seed)}.zip", device="cpu")
        res = eval_multitask(mt, ws, args.n_episodes)
        payload = {"metadata": {"interface": "Cartesian+gripper (same API)",
                                "policy": "single multi-task (reach+pick)",
                                "seed": args.seed, "n_episodes": args.n_episodes,
                                "mt_steps": args.mt_steps, "workspace": str(ws)},
                   "results": res}
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(json.dumps(res, indent=2))
        print(f"\nSaved: {args.output}")
    elif args.mode == "crosseval":
        reach_m = PPO.load(TRAINED / f"so101_cart_reach{_suffix(args.seed)}.zip", device="cpu")
        pick_m = PPO.load(TRAINED / f"so101_cart_pick{_suffix(args.seed)}.zip", device="cpu")
        n = args.n_episodes
        print("cart_reach on reach...")
        rr = eval_on_reach(reach_m, ws, n)
        print("cart_reach on pick (cross)...")
        rp = eval_on_pick(reach_m, ws, n)
        print("cart_pick on reach (cross)...")
        pr = eval_on_reach(pick_m, ws, n)
        print("cart_pick on pick...")
        pp = eval_on_pick(pick_m, ws, n)
        rr_avg = float(np.mean([v["success_rate"] for v in rr.values()]))
        pr_avg = float(np.mean([v["success_rate"] for v in pr.values()]))
        payload = {
            "metadata": {
                "interface": "Cartesian EE delta + gripper (same as LLM/CaP tool layer)",
                "obs": "[ee_xyz(3), gripper_open_frac(1), goal_xyz(3)]",
                "n_episodes_per_reach_variant": n, "n_episodes_pick": n,
                "deterministic": True, "workspace": str(ws), "seed": args.seed,
                "reach_steps": args.reach_steps, "pick_steps": args.pick_steps,
            },
            "results": {
                "cart_reach_on_reach": rr, "cart_reach_on_reach_avg": rr_avg,
                "cart_reach_on_pick": rp,
                "cart_pick_on_reach": pr, "cart_pick_on_reach_avg": pr_avg,
                "cart_pick_on_pick": pp,
            },
        }
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(json.dumps(payload["results"], indent=2))
        print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
