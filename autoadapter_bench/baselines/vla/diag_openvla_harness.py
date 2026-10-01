#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Decisive diagnostic: is OpenVLA's 0/50 a real model failure or a broken
action adapter / harness? No model needed — runs in seconds on a login node.

Two tests on the SAME control path the OpenVLA eval uses
(skel.get_ee_pose -> ee + delta -> skel.move_cartesian):

1. ADAPTER PROBE. From home, command a pure +X, +Y, +Z 5 cm step and measure
   the ACTUAL end-effector displacement. If commanded axis ~= actual axis, the
   adapter/frame is sound (world-aligned Cartesian control works).

2. ORACLE ROLLOUT. Replace OpenVLA's prediction with the GROUND-TRUTH
   direction (target - current ee, clipped) and run the identical 15-step /
   2 cm-tolerance loop on all 5 reach tasks. If the oracle reaches the
   targets, the harness can succeed -> OpenVLA's 0/50 is a real model
   failure. If the oracle ALSO fails, the 0/50 is a harness artifact and the
   baseline is invalid.

Run:
    python autoadapter_bench/baselines/vla/diag_openvla_harness.py \
        --workspace auto_adapter_from_scratch_so101_artifacts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import _load_skeleton_from_workspace
from autoadapter_bench.baselines.vla.eval_openvla import TASKS


def adapter_probe(workspace: Path) -> None:
    print("\n=== ADAPTER PROBE: command pure 5cm axis steps, measure actual EE move ===")
    print(f"{'commanded':<22} {'actual dx,dy,dz (cm)':<28} {'aligned?'}")
    print("-" * 64)
    for axis, vec in [("+X", (0.05, 0, 0)), ("+Y", (0, 0.05, 0)), ("+Z", (0, 0, 0.05))]:
        skel = _load_skeleton_from_workspace(workspace)
        skel.home()
        ee0, _ = skel.get_ee_pose()
        try:
            skel.move_cartesian(ee0 + np.array(vec), duration=0.5)
        except Exception as e:
            print(f"{axis:<22} move_cartesian raised {type(e).__name__}: {e}")
            continue
        ee1, _ = skel.get_ee_pose()
        d = (ee1 - ee0) * 100.0
        want = np.argmax(np.abs(vec))
        aligned = (np.argmax(np.abs(d)) == want) and (np.sign(d[want]) == np.sign(vec[want]))
        print(f"command {axis} 5cm{'':<8} [{d[0]:+6.2f},{d[1]:+6.2f},{d[2]:+6.2f}]{'':<6} "
              f"{'YES' if aligned else 'NO  <-- FRAME BUG'}")


def oracle_rollout(workspace: Path, max_steps: int = 15, tol: float = 0.02,
                   step_cap: float = 0.05) -> None:
    print("\n=== ORACLE ROLLOUT: ground-truth direction through the same loop ===")
    print(f"{'task':<14} {'success':>8} {'final_err(cm)':>14} {'steps':>6}")
    print("-" * 48)
    n_succ = 0
    for name, (instr, offset) in TASKS.items():
        skel = _load_skeleton_from_workspace(workspace)
        skel.home()
        ee0, _ = skel.get_ee_pose()
        target = ee0 + np.array(offset)
        ok, used = False, max_steps
        for step in range(max_steps):
            ee, _ = skel.get_ee_pose()
            err = float(np.linalg.norm(target - ee))
            if err < tol:
                ok, used = True, step
                break
            delta = target - ee
            n = np.linalg.norm(delta)
            if n > step_cap:
                delta = delta / n * step_cap   # clip to a 5cm/step "oracle action"
            try:
                skel.move_cartesian(ee + delta, duration=0.1)
            except Exception:
                pass
        ee, _ = skel.get_ee_pose()
        final_err = float(np.linalg.norm(target - ee))
        if final_err < tol:
            ok = True
        n_succ += int(ok)
        print(f"{name:<14} {('PASS' if ok else 'fail'):>8} {final_err*100:>13.2f} {used:>6}")
    print(f"\nORACLE: {n_succ}/{len(TASKS)} reached target.")
    if n_succ == len(TASKS):
        print("=> Harness is SOUND. OpenVLA's 0/50 is a real model failure (given this control surface).")
    elif n_succ == 0:
        print("=> Harness is BROKEN. The 0/50 is an artifact; OpenVLA baseline is invalid as-is.")
    else:
        print("=> Harness PARTIALLY works; investigate the failing axes before trusting 0/50.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    args = p.parse_args()
    workspace = Path(args.workspace).resolve()
    print(f"workspace: {workspace}")
    adapter_probe(workspace)
    oracle_rollout(workspace)


if __name__ == "__main__":
    main()
