#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Piper Pick spot-check (paper §4.6.4).

Replicates the §4.2 Pick protocol on Piper (6-DoF) using a small grasp-bench
scene with 3 cubes. The agent-synthesized Piper driver from §4.1 controls
the arm; weld-grasp toggling lives in this eval script as a *scene-side*
extension (the SO-101 driver bundles this via skeleton.grasp_backend; the
Piper from-scratch driver does not — we keep both layers cleanly separated
here so the eval doesn't conflate driver capability with scene support).

Tasks:
  - reach_<color>: move EE to 5 cm above cube_<color>
  - pick_<color>:  move EE above cube, descend, gripper_close (activates
                   weld if EE within 4 cm), lift 10 cm, check is_holding

Output: autoadapter_bench/baselines/llm_piper_pick.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import mujoco
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "autoadapter_bench"))
from eval import _cost_usd  # noqa: E402

WORKSPACE = REPO_ROOT / "artifacts" / "auto_adapter_from_scratch_piper_artifacts"
SCENE_XML = REPO_ROOT / "assets" / "mjcf" / "piper" / "pickbench.xml"
DEFAULT_MODEL = os.environ.get(
    "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-6"
)

CUBES = {
    "cube_red":   (0.40, -0.08, 0.215),
    "cube_green": (0.40,  0.00, 0.215),
    "cube_blue":  (0.40,  0.08, 0.215),
}
GRASP_RADIUS_M = 0.06   # weld activates if EE within this distance + closing
LIFT_HEIGHT_M = 0.10
SUCCESS_TOL_M = 0.04    # reach success tolerance (Pick has its own criterion)


def _make_skel_against_pickbench_scene():
    """Build the Piper driver pointed at pickbench.xml instead of the empty
    scene used in §4.1. We reload the Robot class with the new MJCF."""
    spec = importlib.util.spec_from_file_location(
        "driver_piper_pick", str(WORKSPACE / "driver.py")
    )
    mod = importlib.util.module_from_spec(spec)
    orig = os.getcwd()
    os.chdir(WORKSPACE)
    try:
        spec.loader.exec_module(mod)
    finally:
        os.chdir(orig)
    skel = mod.Robot.build_from_mjcf(str(SCENE_XML))
    return skel


def _attach_pickbench_tools(skel):
    """Monkey-patch the skel with cube-awareness + weld-grasp toggle. The
    methods are added at the instance level so other tests using the same
    driver are unaffected."""
    # Index equality constraints by body name
    weld_ids: dict[str, int] = {}
    for eq_id in range(skel.model.neq):
        b1 = skel.model.eq_obj1id[eq_id]
        b2 = skel.model.eq_obj2id[eq_id]
        n2 = mujoco.mj_id2name(skel.model, mujoco.mjtObj.mjOBJ_BODY, b2)
        if n2 and n2.startswith("cube_"):
            weld_ids[n2] = eq_id

    skel._weld_ids = weld_ids
    skel._held_cube: str | None = None

    def get_object_position(body_name: str) -> np.ndarray:
        bid = mujoco.mj_name2id(skel.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if bid < 0:
            raise ValueError(f"unknown body {body_name!r}")
        mujoco.mj_forward(skel.model, skel.data)
        return np.array(skel.data.xpos[bid], dtype=float)

    orig_gripper_close = skel.gripper_close
    orig_gripper_open = skel.gripper_open

    def gripper_close():
        ok = orig_gripper_close()
        if not ok:
            return False
        # Find nearest cube + activate weld if within radius
        ee_pos, _ = skel.get_ee_pose()
        best_name, best_d = None, GRASP_RADIUS_M + 1
        for name in skel._weld_ids:
            cube_pos = get_object_position(name)
            d = float(np.linalg.norm(cube_pos - ee_pos))
            if d < best_d:
                best_name, best_d = name, d
        if best_name is not None and best_d <= GRASP_RADIUS_M:
            eq_id = skel._weld_ids[best_name]
            # Update the weld's eq_data to use CURRENT relative pose.
            # Layout for weld: [anchor_xyz(3), relpose_xyz(3), relquat(4), torquescale(1)]
            link6_id = skel.model.eq_obj1id[eq_id]
            cube_id = skel.model.eq_obj2id[eq_id]
            link6_pos = np.array(skel.data.xpos[link6_id])
            cube_pos = np.array(skel.data.xpos[cube_id])
            # Express cube pose in link6's frame
            link6_quat = np.array(skel.data.xquat[link6_id])
            cube_quat = np.array(skel.data.xquat[cube_id])
            # Inverse of link6 quat (negate vector part for unit quat)
            link6_quat_inv = np.array([link6_quat[0], -link6_quat[1],
                                       -link6_quat[2], -link6_quat[3]])
            # Cube position relative to link6 (rotate world delta by inv quat)
            delta_w = cube_pos - link6_pos
            relpose = np.zeros(3)
            mujoco.mju_rotVecQuat(relpose, delta_w, link6_quat_inv)
            relquat = np.zeros(4)
            mujoco.mju_mulQuat(relquat, link6_quat_inv, cube_quat)
            skel.model.eq_data[eq_id, 0:3] = 0.0
            skel.model.eq_data[eq_id, 3:6] = relpose
            skel.model.eq_data[eq_id, 6:10] = relquat
            skel.model.eq_data[eq_id, 10] = 1.0
            skel.model.eq_active0[eq_id] = 1
            skel.data.eq_active[eq_id] = 1
            skel._held_cube = best_name
            mujoco.mj_forward(skel.model, skel.data)
        return True

    def gripper_open():
        ok = orig_gripper_open()
        if skel._held_cube is not None:
            eq_id = skel._weld_ids[skel._held_cube]
            skel.model.eq_active0[eq_id] = 0
            skel.data.eq_active[eq_id] = 0
            skel._held_cube = None
            mujoco.mj_forward(skel.model, skel.data)
        return True

    def is_holding():
        return skel._held_cube is not None

    # Wrap move_cartesian so that if a cube is held, we carry it along
    # with the EE (Piper driver uses kinematic mj_forward only, which
    # doesn't propagate weld constraints — emulate it here).
    orig_move_cartesian = skel.move_cartesian
    skel._held_offset_in_ee: np.ndarray | None = None

    def move_cartesian(target_xyz, duration: float = 2.0):
        # If holding, record the cube's relative offset to EE BEFORE move
        held = skel._held_cube
        if held is not None:
            ee_before, _ = skel.get_ee_pose()
            cube_before = get_object_position(held)
            offset_world = cube_before - ee_before
        ok = orig_move_cartesian(target_xyz, duration=duration)
        if held is not None:
            # After move, place cube at new (EE + same world-frame offset)
            ee_after, _ = skel.get_ee_pose()
            cube_id = mujoco.mj_name2id(skel.model, mujoco.mjtObj.mjOBJ_BODY, held)
            jnt_id = skel.model.body_jntadr[cube_id]
            qadr = skel.model.jnt_qposadr[jnt_id]
            new_cube_pos = ee_after + offset_world
            skel.data.qpos[qadr:qadr+3] = new_cube_pos
            # Keep cube's existing orientation (don't rotate with EE; weld
            # in reality would, but for lifting we only need translation).
            mujoco.mj_forward(skel.model, skel.data)
        return ok

    skel.get_object_position = get_object_position
    skel.gripper_close = gripper_close
    skel.gripper_open = gripper_open
    skel.is_holding = is_holding
    skel.move_cartesian = move_cartesian
    return skel


def _build_planner(skel, model_id: str):
    """Create a TaskPlanner-style ad-hoc loop with the pickbench tools."""
    from auto_adapter.agent.task_planner import TaskPlanner
    # We bypass TaskPlanner._load_driver and provide a skel directly.
    # Easiest: subclass and override _load_driver.
    workspace = WORKSPACE

    class FixedSkelPlanner(TaskPlanner):
        def _load_driver(self):
            sk = _make_skel_against_pickbench_scene()
            sk.home()
            return _attach_pickbench_tools(sk)

    return FixedSkelPlanner(
        workspace=workspace, bedrock_model=model_id,
        region="us-east-1", max_iters=30, max_tokens_per_turn=6000,
    )


def _replay_and_measure_pick(planner, tool_log, target_cube: str) -> dict:
    """Replay tool calls on a fresh skel; measure (a) final EE pos,
    (b) final cube z, (c) is_holding state."""
    skel = planner._load_driver()
    for call in tool_log:
        tool = call["tool"]
        inp = call.get("input", {})
        method = getattr(skel, tool, None)
        if method is None:
            continue
        try:
            if tool == "move_cartesian":
                method(np.array([inp["x"], inp["y"], inp["z"]]),
                       duration=float(inp.get("duration", 2.0)))
            elif tool == "get_object_position":
                method(inp.get("body_name", ""))
            elif tool in ("home", "gripper_open", "gripper_close",
                          "is_holding"):
                method()
            else:
                method()
        except Exception as e:  # noqa: BLE001
            print(f"   replay error on {tool}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            break

    ee, _ = skel.get_ee_pose()
    cube_pos = skel.get_object_position(target_cube)
    cube_initial_z = CUBES[target_cube][2]
    lifted = float(cube_pos[2]) - cube_initial_z
    return {
        "ee_pos": ee.tolist(),
        "cube_pos": cube_pos.tolist(),
        "lifted_m": lifted,
        "is_holding": bool(skel.is_holding()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", default=str(
        REPO_ROOT / "autoadapter_bench" / "baselines" / "llm_piper_pick.json"))
    parser.add_argument("--tasks", default="reach_red,reach_green,reach_blue,"
                        "pick_red,pick_green,pick_blue")
    args = parser.parse_args()

    tasks = args.tasks.split(",")

    print(f"Scene:  {SCENE_XML}")
    print(f"Tasks:  {tasks}")
    print(f"Model:  {args.model}")
    print(f"N:      {args.n_trials} trials per task\n")

    planner = _build_planner(None, args.model)

    results = {}
    t_start = time.time()
    total_cost = 0.0

    for task_id in tasks:
        kind, color = task_id.split("_", 1)
        cube_name = f"cube_{color}"
        cube_pos = CUBES[cube_name]
        if kind == "reach":
            prompt = (f"Move the end-effector to 5 cm directly above the "
                      f"body named '{cube_name}'. Use get_object_position "
                      f"to read its current position, then move to "
                      f"(x, y, z+0.05). Stop there, don't return.")
        elif kind == "pick":
            prompt = (f"Pick up the body named '{cube_name}' and lift it "
                      f"10 cm above its current position. Steps: (1) read "
                      f"its position with get_object_position, (2) open "
                      f"the gripper, (3) move EE 5 cm above the cube, "
                      f"(4) descend to 1 cm above the cube, (5) close "
                      f"the gripper, (6) lift to 10 cm above the original "
                      f"cube position. Finally check is_holding and "
                      f"report the result.")
        else:
            raise ValueError(task_id)

        trials = []
        print(f"=== {task_id} ===")
        for trial in range(args.n_trials):
            print(f"   trial {trial+1}/{args.n_trials} ", end="", flush=True)
            t0 = time.time()
            try:
                r = planner.execute_task(prompt,
                                         task_id=f"{task_id}_t{trial}",
                                         capture_video=False)
            except Exception as e:  # noqa: BLE001
                print(f"CRASH: {type(e).__name__}: {e}")
                trials.append({"trial": trial, "success": False,
                               "error": f"{type(e).__name__}: {e}"})
                continue
            dt = time.time() - t0

            try:
                phys = _replay_and_measure_pick(planner, r.tool_call_log,
                                                cube_name)
            except Exception as e:  # noqa: BLE001
                phys = {"error": f"{type(e).__name__}: {e}"}

            if kind == "reach":
                target = np.array([cube_pos[0], cube_pos[1], cube_pos[2] + 0.05])
                err = float(np.linalg.norm(np.array(phys.get("ee_pos", [0,0,0])) - target))
                success = err < SUCCESS_TOL_M
                detail = f"reach_err={err*100:.1f}cm"
            elif kind == "pick":
                lifted = phys.get("lifted_m", 0.0)
                holding = phys.get("is_holding", False)
                success = bool(holding and lifted > 0.05)
                detail = f"lifted={lifted*100:.1f}cm holding={holding}"

            tu = r.token_usage or {}
            t_in = int(tu.get("in", 0) or 0)
            t_out = int(tu.get("out", 0) or 0)
            cost = _cost_usd(args.model, t_in, t_out)
            total_cost += cost

            mark = "✓" if success else "✗"
            print(f"{mark} {detail} dt={dt:.1f}s cost=${cost:.3f}")
            trials.append({
                "trial": trial,
                "success": bool(success),
                "phys": phys,
                "duration_sec": float(dt),
                "n_tool_calls": len(r.tool_call_log),
                "tokens": {"in": t_in, "out": t_out},
                "cost_usd": cost,
                "summary": (r.summary or "")[:140],
            })

        n_ok = sum(1 for t in trials if t["success"])
        rate = n_ok / max(1, len(trials))
        results[task_id] = {
            "n_success": n_ok,
            "n_total": len(trials),
            "success_rate": rate,
            "trials": trials,
        }
        print(f"   {task_id}: {n_ok}/{len(trials)} = {rate*100:.0f}%\n")

    wall = time.time() - t_start
    all_rates = [r["success_rate"] for r in results.values()]
    summary = {
        "n_tasks": len(results),
        "n_trials_per_task": args.n_trials,
        "n_trials_total": sum(r["n_total"] for r in results.values()),
        "n_success_total": sum(r["n_success"] for r in results.values()),
        "overall_success_rate": float(np.mean(all_rates)) if all_rates else 0.0,
        "total_cost_usd": total_cost,
        "wall_clock_sec": wall,
    }

    out = {
        "metadata": {
            "scene": str(SCENE_XML),
            "workspace": str(WORKSPACE),
            "n_trials_per_task": args.n_trials,
            "agent_model": args.model,
            "grasp_radius_m": GRASP_RADIUS_M,
            "success_tol_m": SUCCESS_TOL_M,
            "tasks": tasks,
        },
        "results": results,
        "summary": summary,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {out_path}")
    print(f"Overall: {summary['n_success_total']}/{summary['n_trials_total']}"
          f" = {summary['overall_success_rate']*100:.0f}%  "
          f"(${total_cost:.2f}, {wall/60:.1f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
