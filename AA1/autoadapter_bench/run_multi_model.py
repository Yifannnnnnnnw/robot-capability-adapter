#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Multi-model benchmark runner — invokes eval.py for each
(robot, model) combination, writes per-run JSONs the leaderboard ingests.

Usage:
    python autoadapter_bench/run_multi_model.py --preset smoke
    python autoadapter_bench/run_multi_model.py --preset headline
    python autoadapter_bench/run_multi_model.py --preset full
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PRESETS = {
    "smoke": {
        "robots": ["so101", "go2"],
        "suites": "simple",
        "tasks_arm": "move_x_plus_5cm,trace_square_xy,gripper_cycle",
        "tasks_quadruped": "stand_up,sit,walk_forward_1s",
        "n_trials": 2,
        "models": [
            ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "sonnet45"),
            ("us.anthropic.claude-sonnet-4-6",              "sonnet46"),
            ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "haiku45"),
        ],
    },
    "headline": {
        "robots": ["so101", "franka", "go2", "anymal_c"],
        "suites": "simple,hard",
        "tasks_arm": None,   # all in suite
        "tasks_quadruped": None,
        "n_trials": 3,
        "models": [
            ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "sonnet45"),
            ("us.anthropic.claude-sonnet-4-6",              "sonnet46"),
            ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "haiku45"),
        ],
    },
    "full": {
        "robots": ["so101", "piper", "franka", "ur5e", "kuka_iiwa14",
                   "go2", "unitree_a1", "anymal_c"],
        "suites": "simple,hard,contact_rich",
        "tasks_arm": None,
        "tasks_quadruped": None,
        "n_trials": 5,
        "models": [
            ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "sonnet45"),
            ("us.anthropic.claude-sonnet-4-6",              "sonnet46"),
            ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "haiku45"),
            ("us.anthropic.claude-opus-4-8",                "opus48"),
        ],
    },
}


def get_robot_class(robot_id: str) -> str:
    """Look up robot class from the zoo yaml without yaml import here."""
    spec = (REPO_ROOT / "autoadapter_bench" / "spec" / "robot_zoo.yaml").read_text()
    # naive scan
    current_id = None
    for line in spec.splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            current_id = s.split(":", 1)[1].strip()
        elif s.startswith("class:") and current_id == robot_id:
            return s.split(":", 1)[1].strip()
    raise ValueError(f"class not found for {robot_id!r}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--preset", choices=list(PRESETS), default="smoke")
    p.add_argument("--results-dir",
                   default=str(REPO_ROOT / "autoadapter_bench" / "results"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cfg = PRESETS[args.preset]
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    runs: list[tuple[str, str, str, str]] = []   # (model_id, model_short, robot, output_path)
    for model_id, model_short in cfg["models"]:
        for robot in cfg["robots"]:
            output = results_dir / f"{args.preset}_{robot}_{model_short}.json"
            runs.append((model_id, model_short, robot, str(output)))

    total = len(runs)
    print(f"Preset: {args.preset}")
    print(f"  {total} (robot × model) runs to execute")
    print(f"  models: {[m[1] for m in cfg['models']]}")
    print(f"  robots: {cfg['robots']}")
    print(f"  suites: {cfg['suites']}, n_trials: {cfg['n_trials']}")
    print()

    if args.dry_run:
        for i, (model, short, robot, out) in enumerate(runs):
            print(f"  [{i+1}/{total}] {short:10s} × {robot:12s} → {Path(out).name}")
        return

    t_start = time.time()
    for i, (model_id, short, robot, out) in enumerate(runs):
        cls = get_robot_class(robot)
        task_key = "tasks_arm" if cls == "arm" else "tasks_quadruped"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "autoadapter_bench" / "eval.py"),
            "--robot", robot,
            "--suites", cfg["suites"],
            "--model", model_id,
            "--n-trials", str(cfg["n_trials"]),
            "--output", out,
        ]
        if cfg.get(task_key):
            cmd += ["--tasks", cfg[task_key]]

        elapsed = time.time() - t_start
        print(f"\n[{i+1}/{total}] {short:10s} × {robot:12s}  "
              f"(elapsed {elapsed:.0f}s, output {Path(out).name})")
        # Stream subprocess output (eval.py prints per-trial status)
        try:
            rc = subprocess.call(cmd, cwd=str(REPO_ROOT))
            print(f"  exit={rc}")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(1)

    total_t = time.time() - t_start
    print(f"\n=== ALL RUNS COMPLETE ===")
    print(f"  total wall clock: {total_t:.0f}s ({total_t/60:.1f} min)")

    # Roll up
    print(f"\nGenerating leaderboard...")
    lb_cmd = [sys.executable,
              str(REPO_ROOT / "autoadapter_bench" / "leaderboard.py"),
              "--results-dir", str(results_dir),
              "--output", str(REPO_ROOT / "autoadapter_bench" / f"LEADERBOARD_{args.preset}.md")]
    subprocess.call(lb_cmd, cwd=str(REPO_ROOT))


if __name__ == "__main__":
    main()
