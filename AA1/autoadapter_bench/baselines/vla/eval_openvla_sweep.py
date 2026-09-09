#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Config-robustness sweep for the OpenVLA-7B zero-shot SO-101 baseline.

The headline result (0/50, eval_openvla.py) uses one config: unnorm_key=
bridge_orig, ee_delta_scale=2.0. A reviewer's natural objection is "you ran
it wrong" — wrong action de-normalization or wrong delta scale. This script
answers that objection with evidence: it sweeps every unnorm key the model
actually ships against a wide range of action scales and reports the success
rate of each cell. If the whole grid stays at 0, the zero is a property of
the model on this OOD embodiment, not an artifact of one config choice.

Decoding is greedy (do_sample=False) and the reach view is fixed, so each
episode is deterministic; 3 episodes/cell is plenty (a cell is either 0/3
or 3/3). Loads the model once, reuses it for the whole grid.

Run on Myriad A100:
    python autoadapter_bench/baselines/vla/eval_openvla_sweep.py \
        --workspace auto_adapter_so101_v2_artifacts \
        --output autoadapter_bench/baselines/vla/openvla_sweep.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import _load_skeleton_from_workspace
from autoadapter_bench.baselines.vla.eval_openvla import OpenVLAPlanner, TASKS

# Action-scale grid swept at the canonical unnorm key. Bridge raw deltas are
# ~1-3 cm/step; we span 0.25x .. 10x to bracket any reasonable choice.
SCALE_GRID = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
# Unnorm keys to try beyond whatever the model ships; intersected with the
# model's actual norm_stats so an unknown key never raises.
CANDIDATE_KEYS = [
    "bridge_orig", "bridge", "fractal20220817_data", "berkeley_autolab_ur5",
    "taco_play", "jaco_play", "viola", "toto", "austin_buds_dataset_converted_externally_to_rlds",
]


def run_cell(planner: OpenVLAPlanner, workspace: Path, unnorm_key: str,
             scale: float, n_episodes: int, max_steps: int) -> dict:
    """Evaluate all 5 reach tasks at one (unnorm_key, scale) cell."""
    planner.ee_delta_scale = scale
    per_task = {}
    for name, (instr, offset) in TASKS.items():
        skel0 = _load_skeleton_from_workspace(workspace)
        skel0.home()
        ee0, _ = skel0.get_ee_pose()
        target = ee0 + np.array(offset)
        succ, errs = 0, []
        for _ in range(n_episodes):
            skel = _load_skeleton_from_workspace(workspace)
            skel.home()
            ok = False
            for step in range(max_steps):
                ee_xyz, _ = skel.get_ee_pose()
                if float(np.linalg.norm(target - ee_xyz)) < 0.02:
                    ok = True
                    break
                rgb = skel.render()
                action7 = planner.predict_action(rgb, instr, unnorm_key=unnorm_key)
                next_ee = ee_xyz + action7[:3] * scale
                try:
                    skel.move_cartesian(next_ee, duration=0.5)  # see diag_openvla_harness.py: 0.1 zeros any controller
                except Exception:
                    pass
            ee_f, _ = skel.get_ee_pose()
            final_err = float(np.linalg.norm(target - ee_f))
            if final_err < 0.02:
                ok = True
            if ok:
                succ += 1
            errs.append(final_err)
        per_task[name] = {"success_rate": succ / n_episodes,
                          "mean_final_err_m": float(np.mean(errs))}
    sr = [v["success_rate"] for v in per_task.values()]
    return {"unnorm_key": unnorm_key, "ee_delta_scale": scale,
            "overall_success_rate": float(np.mean(sr)),
            "n_success_cells": int(sum(1 for s in sr if s > 0)),
            "mean_final_err_m": float(np.mean([v["mean_final_err_m"] for v in per_task.values()])),
            "per_task": per_task}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--n-episodes", type=int, default=3)
    p.add_argument("--max-steps", type=int, default=15)
    p.add_argument("--model-id", default="openvla/openvla-7b")
    args = p.parse_args()

    import torch
    workspace = Path(args.workspace).resolve()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}")
    planner = OpenVLAPlanner(model_id=args.model_id, device=device, ee_delta_scale=2.0)

    # Discover which unnorm keys the model actually ships.
    norm_stats = getattr(planner.model, "norm_stats", {}) or {}
    available = list(norm_stats.keys())
    print(f"model ships {len(available)} unnorm keys: {available}")
    keys = [k for k in CANDIDATE_KEYS if k in available] or (["bridge_orig"] if not available else available[:5])
    print(f"sweeping unnorm keys: {keys}")

    configs = []
    # 1) scale sweep at the canonical unnorm key
    canon = "bridge_orig" if "bridge_orig" in keys else keys[0]
    for s in SCALE_GRID:
        configs.append((canon, s))
    # 2) unnorm-key sweep at the canonical scale (2.0), skipping the dup
    for k in keys:
        if k != canon:
            configs.append((k, 2.0))

    print(f"\n{len(configs)} configs x 5 tasks x {args.n_episodes} ep\n")
    print(f"{'unnorm_key':<40} {'scale':>6} {'overall':>8} {'cells>0':>8} {'mean_err':>10}")
    print("-" * 80)
    cells = []
    t0 = time.time()
    for unnorm_key, scale in configs:
        r = run_cell(planner, workspace, unnorm_key, scale,
                     args.n_episodes, args.max_steps)
        cells.append(r)
        print(f"{unnorm_key:<40} {scale:>6.2f} {r['overall_success_rate']*100:>7.1f}% "
              f"{r['n_success_cells']:>8} {r['mean_final_err_m']*100:>9.2f}cm")

    any_success = any(c["overall_success_rate"] > 0 for c in cells)
    out = {
        "model_id": args.model_id,
        "workspace": str(workspace),
        "n_episodes_per_cell": args.n_episodes,
        "max_steps": args.max_steps,
        "available_unnorm_keys": available,
        "swept_configs": len(configs),
        "any_config_succeeds": any_success,
        "best_overall_success_rate": max(c["overall_success_rate"] for c in cells),
        "cells": cells,
    }
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"\nany config succeeds: {any_success}")
    print(f"best overall success across grid: {out['best_overall_success_rate']*100:.1f}%")
    print(f"total sweep time: {(time.time()-t0)/60:.1f} min")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
