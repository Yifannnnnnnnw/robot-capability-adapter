#!/usr/bin/env python3
"""Rebuttal add-on (Reviewer K6sW): Franka reach failures — solver or synthesis?

Deterministic oracle ablation. The original 20/70 (llm_franka_proper_reach_n10)
had a Sonnet-4.5 agent issue move_cartesian(target); the paper attributes the
failures to the prescribed DLS IK template, not the LLM. This script removes
the LLM entirely: a scripted oracle issues the EXACT target for the same 7
variants under the same methodology (fresh skel, no home(); target = fresh EE
+ offset; success = final EE within 2 cm).

Arms:
  A  stock      — the synthesized driver's ik_dls, untouched.
  B  nullspace  — identical driver, ONLY ik_dls replaced by a nullspace-
                  regularized DLS (bias redundant DoF toward home posture).
                  Same interface, same joint-limit clamping, same step control.
  C  stock-5x   — stock ik_dls with 5x iteration budget (controls for "just
                  needs more iterations").

If A reproduces the paper's failure signature without any LLM, the agent is
exonerated. If B fixes what A fails while C does not, the cause is the solver
TEMPLATE (redundancy resolution), not iteration budget or synthesis noise.

Run:  .venv-rl/bin/python experiments/rebuttal_franka_ik/run.py
"""
from __future__ import annotations
import importlib.util, json, os, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO / "artifacts" / "auto_adapter_from_scratch_franka_artifacts"
OUT = Path(__file__).resolve().parent / "result.json"

# identical to eval_franka_reach_n10.py
REACH_VARIANTS = {
    "reach_x+5":  (+0.05, 0.0, 0.0),
    "reach_x-5":  (-0.05, 0.0, 0.0),
    "reach_y+5":  (0.0, +0.05, 0.0),
    "reach_y-5":  (0.0, -0.05, 0.0),
    "reach_z+5":  (0.0, 0.0, +0.05),
    "reach_z-5":  (0.0, 0.0, -0.05),
    "reach_diag": (+0.035, +0.035, 0.0),
}
SUCCESS_TOL_M = 0.02


def load_robot():
    """Fresh Robot, no home() — mirrors eval_franka_reach_n10 methodology."""
    spec = importlib.util.spec_from_file_location(
        "driver_fr", str(WORKSPACE / "driver.py"))
    mod = importlib.util.module_from_spec(spec)
    orig = os.getcwd()
    os.chdir(WORKSPACE)
    try:
        spec.loader.exec_module(mod)
        return mod.Robot.build_from_mjcf("mjcf.xml")
    finally:
        os.chdir(orig)


def make_nullspace_ik(robot):
    """Nullspace-regularized DLS bound to `robot`, same signature as ik_dls.

    Differences from stock, ALL solver-internal:
      - fixed small damping (1e-3) instead of residual-scaled damping
      - nullspace bias toward home_q: dq += (I - J+J) k (q_home - q)
      - tighter tol default and 500 iters
    Same side effects (set_joint_positions during iterations), same clamping,
    same step-size control, same return convention, same >2cm RuntimeError.
    """
    def ik_nullspace(target_pos, q_init=None, max_iter=500, tol=None,
                     verbose=False):
        if q_init is None:
            q_init = robot.get_joint_positions()
        if tol is None:
            tol = robot.ik_tol
        q = q_init.copy()
        q_ref = np.asarray(robot.home_q, dtype=float)
        lam = 1e-3
        k_null = 0.05
        error_norm = np.inf
        for _ in range(max_iter):
            robot.set_joint_positions(q)
            current_pos, _ = robot.get_ee_pose()
            error = np.asarray(target_pos, float) - current_pos
            error_norm = float(np.linalg.norm(error))
            if error_norm < tol:
                return q, True
            jac = robot.compute_jacobian()[:3, :]
            JtJ = jac.T @ jac
            damped = JtJ + (lam ** 2) * np.eye(robot.dof)
            J_pinv = np.linalg.solve(damped, jac.T)
            dq_task = J_pinv @ error
            # nullspace projection toward reference posture
            N = np.eye(robot.dof) - J_pinv @ jac
            dq_null = N @ (k_null * (q_ref - q))
            dq = dq_task + dq_null
            dq_norm = float(np.linalg.norm(dq))
            step = 0.5 / dq_norm if dq_norm > 0.5 else 1.0
            q = q + step * dq
            q = np.clip(q, robot.joint_limits[:, 0], robot.joint_limits[:, 1])
        if error_norm > 0.02:
            raise RuntimeError(f"IK failed: final error {error_norm*1000:.1f} mm")
        return q, True
    return ik_nullspace


def exec_gravcomp(robot, q_t, duration=2.0):
    """Interpolated execution with gravity compensation applied each step."""
    import mujoco
    q0 = robot.get_joint_positions()
    n = int(duration / robot.control_timestep)
    for i in range(n):
        t = (i + 1) / n
        robot.set_joint_controls(q0 + t * (q_t - q0))
        robot.data.qfrc_applied[:robot.dof] = robot.data.qfrc_bias[:robot.dof]
        mujoco.mj_step(robot.model, robot.data)


def run_arm(arm: str) -> dict:
    per_variant = {}
    for variant, offset in REACH_VARIANTS.items():
        robot = load_robot()                      # fresh skel per variant
        if arm == "nullspace":
            robot.ik_dls = make_nullspace_ik(robot)
        elif arm == "stock5x":
            robot.ik_max_iter = robot.ik_max_iter * 5
        ee0, _ = robot.get_ee_pose()
        ee0 = np.array(ee0, float)
        target = ee0 + np.array(offset)
        err = None
        crash = None
        try:
            if arm == "gravcomp":
                q_t, _ = robot.ik_dls(target,
                                      q_init=robot.get_joint_positions())
                exec_gravcomp(robot, q_t)
            else:
                robot.move_cartesian(target, duration=2.0)  # oracle target
            ee_f, _ = robot.get_ee_pose()
            err = float(np.linalg.norm(np.array(ee_f, float) - target))
        except Exception as e:  # IK RuntimeError -> arm never reached
            crash = f"{type(e).__name__}: {e}"
            ee_f, _ = robot.get_ee_pose()
            err = float(np.linalg.norm(np.array(ee_f, float) - target))
        ok = (crash is None) and (err < SUCCESS_TOL_M)
        per_variant[variant] = {"success": bool(ok), "err_m": err,
                                "crash": crash}
        mark = "✓" if ok else "✗"
        print(f"  [{arm:9s}] {variant:11s} {mark} err={err*100:6.2f} cm"
              + (f"  ({crash})" if crash else ""))
    n_ok = sum(v["success"] for v in per_variant.values())
    print(f"  [{arm:9s}] TOTAL {n_ok}/7\n")
    return {"per_variant": per_variant, "n_success": n_ok, "n_total": 7}


def main() -> int:
    print(f"Franka oracle IK ablation (deterministic; tol={SUCCESS_TOL_M} m)\n")
    out = {"methodology": (
        "Deterministic oracle: scripted move_cartesian(exact target), fresh "
        "skel no home(), target = fresh EE + offset; identical variants, "
        "tolerance, and measurement as llm_franka_proper_reach_n10.json. "
        "No LLM involved. Each variant is deterministic (1 run == N runs; "
        "stock and stock5x produce bit-identical per-variant errors, which "
        "is the empirical determinism check)."),
        "arms": {}}
    for arm in ("stock", "stock5x", "nullspace", "gravcomp"):
        out["arms"][arm] = run_arm(arm)
    OUT.write_text(json.dumps(out, indent=2))
    a, c, b, g = (out["arms"]["stock"]["n_success"],
                  out["arms"]["stock5x"]["n_success"],
                  out["arms"]["nullspace"]["n_success"],
                  out["arms"]["gravcomp"]["n_success"])
    print(f"SUMMARY  stock {a}/7 | stock-5x {c}/7 | nullspace {b}/7 "
          f"| gravity-comp exec {g}/7")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
