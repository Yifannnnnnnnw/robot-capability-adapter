#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Evaluate OpenVLA-7B on the 5 SO-101 reach-task variants.

This baseline answers: "does a state-of-the-art VLA model — trained on a wide
mixture of embodiments via Open-X-Embodiment — generalize zero-shot to a
robot it has never seen?"

OpenVLA is trained primarily on Bridge V2 (WidowX 250) and the Open-X mixture.
SO-101 is a 5-DOF arm with a different morphology and not part of the training
distribution. We map the predicted 7-DOF action's Cartesian-delta component
to SO-101's coordinate frame via IK.

This is the same control surface our LLM agent uses (Cartesian deltas via
move_cartesian / ik) — so the comparison isolates "VLA predicted dxdydz" vs
"LLM-predicted dxdydz" with identical downstream execution.

Run with the sibling venv:
    VIRTUAL_ENV=.venv-openvla PYTHONPATH=. .venv-openvla/bin/python \
        autoadapter_bench/baselines/vla/eval_openvla.py \
        --workspace artifacts/auto_adapter_so101_v2_artifacts \
        --output autoadapter_bench/baselines/vla/openvla_so101_reach_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForVision2Seq, AutoProcessor

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from autoadapter_bench.baselines.rl.rl_env import _load_skeleton_from_workspace


# ──────────────────────────────────────────────────────────────────────────
# OpenVLA inference
# ──────────────────────────────────────────────────────────────────────────


class OpenVLAPlanner:
    """Wrap OpenVLA-7B for tabletop reach tasks.

    Action mapping: OpenVLA outputs (dx, dy, dz, drx, dry, drz, gripper) in
    a Bridge-V2-normalized frame. We apply only the (dx, dy, dz) cartesian
    delta to the current SO-101 EE pose; rotation is ignored (SO-101 wrist
    is unconstrained here) and gripper is irrelevant for reach.
    """

    PROMPT_TEMPLATE = "In: What action should the robot take to {instruction}?\nOut:"

    def __init__(self, model_id: str = "openvla/openvla-7b",
                  device: str = "cpu",
                  ee_delta_scale: float = 1.0) -> None:
        print(f"loading OpenVLA from {model_id} on {device}...")
        t0 = time.time()
        self.processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        self.model = AutoModelForVision2Seq.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        ).to(device)
        self.model.eval()
        self.device = device
        self.ee_delta_scale = ee_delta_scale
        print(f"  loaded in {time.time()-t0:.1f}s")

    def predict_action(self, rgb: np.ndarray, instruction: str,
                        unnorm_key: str = "bridge_orig") -> np.ndarray:
        """Returns 7-vec: (dx, dy, dz, drx, dry, drz, gripper)."""
        img = Image.fromarray(rgb)
        prompt = self.PROMPT_TEMPLATE.format(instruction=instruction)
        inputs = self.processor(prompt, img).to(self.device, dtype=torch.bfloat16)
        with torch.no_grad():
            action = self.model.predict_action(**inputs, unnorm_key=unnorm_key, do_sample=False)
        return np.array(action, dtype=np.float64)


# ──────────────────────────────────────────────────────────────────────────
# Roll out one episode
# ──────────────────────────────────────────────────────────────────────────


TASKS = {
    "in_dist_x+5":  ("move the gripper forward",                            (+0.05, 0.0, 0.0)),
    "ood_y+5":      ("move the gripper to the left",                         (0.0, +0.05, 0.0)),
    "ood_z+5":      ("lift the gripper upward",                              (0.0, 0.0, +0.05)),
    "ood_diag":     ("move the gripper forward, left and up",                (+0.03, +0.03, +0.03)),
    "ood_neg_x":    ("move the gripper backward",                            (-0.05, 0.0, 0.0)),
}


def rollout_one_episode(planner: OpenVLAPlanner, skel, instruction: str,
                         target_xyz: np.ndarray, max_steps: int = 20,
                         success_tol: float = 0.02) -> dict:
    """One episode: render → predict → apply delta → step. Loop until success
    or max_steps. IK failures are logged but DON'T break the loop — try the
    next predicted action instead."""
    skel.home()
    trajectory = []
    for step in range(max_steps):
        rgb = skel.render()
        ee_xyz, _ = skel.get_ee_pose()
        err = float(np.linalg.norm(target_xyz - ee_xyz))
        if err < success_tol:
            return {"success": True, "final_err_m": err, "steps": step,
                     "trajectory": trajectory}
        t0 = time.time()
        action_7 = planner.predict_action(rgb, instruction)
        infer_time = time.time() - t0
        cart_delta = action_7[:3] * planner.ee_delta_scale
        next_ee = ee_xyz + cart_delta
        ik_failed = None
        try:
            # Use move_cartesian (high-level Cartesian skill — internal IK + interp).
            # Works on BOTH skeleton-based and from-scratch drivers.
            # duration must be long enough for the controller to settle: an
            # oracle (ground-truth direction) reaches the 2cm tolerance at
            # duration>=0.3 but NEVER at 0.1 (plateaus ~4cm) — see
            # diag_openvla_harness.py. 0.1 would zero out any controller.
            skel.move_cartesian(next_ee, duration=0.5)
        except Exception as e:
            ik_failed = f"{type(e).__name__}: {e}"
            # Fall back to ik() if available (skeleton driver path)
            try:
                if hasattr(skel, "ik"):
                    q_des = skel.ik(next_ee, q_init=skel.get_joint_positions(),
                                     raise_on_unreachable=False)
                    skel.set_arm_actuators(q_des)
                    skel.step(20)
                elif hasattr(skel, "inverse_kinematics"):
                    q_des = skel.inverse_kinematics(next_ee,
                                                    q_init=skel.get_joint_positions())
                    skel.set_joint_positions(q_des)
                    skel.step(20)
            except Exception:
                pass
        trajectory.append({
            "step": step, "ee_xyz": ee_xyz.tolist(),
            "action_xyz": cart_delta.tolist(),
            "next_ee_target": next_ee.tolist(),
            "infer_time_sec": infer_time,
            "ik_failed": ik_failed,
        })

    ee_final, _ = skel.get_ee_pose()
    final_err = float(np.linalg.norm(target_xyz - ee_final))
    return {"success": bool(final_err < success_tol), "final_err_m": final_err,
             "steps": max_steps, "trajectory": trajectory}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--n-episodes", type=int, default=3,
                   help="OpenVLA is deterministic given image; few eps suffice")
    p.add_argument("--max-steps", type=int, default=15,
                   help="Inference per step is slow on CPU")
    p.add_argument("--ee-delta-scale", type=float, default=2.0,
                   help="Scale OpenVLA's predicted cartesian delta. Default 2.0 "
                        "(canonical — matches paper §4.5 and the OpenVLA result "
                        "JSON in autoadapter_bench/baselines/vla/openvla_so101_reach_eval.json). "
                        "Bridge V2 raw outputs ~1-3cm/step; SO-101 has a larger workspace, "
                        "so we double to ~3-6cm/step on average.")
    p.add_argument("--model-id", default="openvla/openvla-7b")
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    if torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    print(f"using device: {device}")
    planner = OpenVLAPlanner(model_id=args.model_id, device=device,
                              ee_delta_scale=args.ee_delta_scale)

    results = {}
    print(f"\nEvaluating OpenVLA on {len(TASKS)} task variants "
           f"({args.n_episodes} ep each, ≤{args.max_steps} steps):")
    print(f"{'Task':<14} {'success':>8} {'mean_err':>10} {'mean_steps':>11} {'time':>8}")
    print("-" * 60)
    for name, (instr, offset) in TASKS.items():
        # Compute target from one fresh skel (HOME ee + offset)
        skel0 = _load_skeleton_from_workspace(workspace)
        skel0.home()
        ee0, _ = skel0.get_ee_pose()
        target = ee0 + np.array(offset)

        succs, errs, all_steps, ep_records = 0, [], 0, []
        t_task = time.time()
        for ep in range(args.n_episodes):
            skel = _load_skeleton_from_workspace(workspace)
            res = rollout_one_episode(planner, skel, instr, target,
                                       max_steps=args.max_steps)
            if res["success"]:
                succs += 1
            errs.append(res["final_err_m"])
            all_steps += res["steps"]
            ep_records.append({
                "success": res["success"], "final_err_m": res["final_err_m"],
                "steps": res["steps"],
                "trajectory": res.get("trajectory", []),
            })
        task_time = time.time() - t_task
        results[name] = {
            "instruction": instr,
            "target_offset": list(offset),
            "n_episodes": args.n_episodes,
            "success_rate": succs / args.n_episodes,
            "mean_final_err_m": float(np.mean(errs)),
            "mean_steps_per_episode": all_steps / args.n_episodes,
            "task_eval_time_sec": task_time,
            "episodes": ep_records,
        }
        print(f"{name:<14} {results[name]['success_rate']*100:>7.1f}% "
              f"{results[name]['mean_final_err_m']*100:>9.2f}cm "
              f"{results[name]['mean_steps_per_episode']:>10.1f} "
              f"{task_time:>7.0f}s")

    in_dist = results["in_dist_x+5"]["success_rate"]
    ood = [v["success_rate"] for k, v in results.items() if k != "in_dist_x+5"]
    ood_avg = sum(ood) / max(len(ood), 1)
    print()
    print(f"  In-distribution (forward): {in_dist*100:.0f}%")
    print(f"  OOD avg: {ood_avg*100:.0f}%")
    print(f"  Generalization gap: {(in_dist - ood_avg)*100:.0f}pp")

    Path(args.output).write_text(json.dumps({
        "model_id": args.model_id,
        "ee_delta_scale": args.ee_delta_scale,
        "n_episodes_per_task": args.n_episodes,
        "in_distribution_success_rate": in_dist,
        "ood_avg_success_rate": ood_avg,
        "generalization_gap_pp": (in_dist - ood_avg) * 100,
        "per_task": results,
    }, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
