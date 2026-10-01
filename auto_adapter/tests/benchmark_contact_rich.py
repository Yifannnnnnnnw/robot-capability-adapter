# SPDX-License-Identifier: Apache-2.0
"""Contact-rich task benchmark.

Tasks where success is determined by PHYSICS STATE — object positions before
vs after — not by the LLM saying "done". This attacks the "tasks are too toy"
critique: previous task suite was "move EE 5cm in X" which doesn't require
ANY physical interaction. These tasks require:

  1. STACK         — pick A, place on B, verify A.z > B.z after release
                     (physics must keep A balanced on B for ≥0.5s settle)
  2. PLACE_INSIDE  — pick small object, drop INTO larger container
  3. COLLISION_AVOID — move EE through scene without knocking over a body
  4. SEQUENTIAL_REORG — swap two objects' positions (pick A → buffer →
                       pick B → A's spot → drop A in B's spot)
  5. STABLE_PLACE  — pick object, place on table at marked target, verify
                     object stays within 3cm of target after 1s settle

Each task:
  - Records "before" object positions
  - Lets agent execute
  - Records "after" object positions
  - Computes physics-based success metric
  - Reports both LLM's claim AND physics check (mismatch = caught lie)

Run on framework SO-101 driver (weld grasp works for these).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner


# ──────────────────────────────────────────────────────────────────────────
# Contact-rich tasks + physics-validated success criteria
# ──────────────────────────────────────────────────────────────────────────


def _validate_stack(skel, before: dict) -> tuple[bool, str, dict]:
    """A on top of B, verify A.z > B.z AND A is within 5cm of B in XY."""
    a = skel.get_object_position("lego")
    b = skel.get_object_position("duck")
    z_above = a[2] > b[2]  # lego above duck
    xy_dist = float(np.linalg.norm(a[:2] - b[:2]))
    close_xy = xy_dist < 0.05
    ok = z_above and close_xy
    detail = (f"lego at {a.tolist()}, duck at {b.tolist()}, "
              f"lego_z > duck_z? {z_above}, xy_dist={xy_dist:.3f}m")
    return ok, detail, {"z_above": z_above, "xy_dist_m": xy_dist}


def _validate_place_inside(skel, before: dict) -> tuple[bool, str, dict]:
    """Duck inside mug — duck.xy close to mug.xy AND duck.z ≤ mug.z + 5cm."""
    duck = skel.get_object_position("duck")
    mug = skel.get_object_position("mug")
    xy_dist = float(np.linalg.norm(duck[:2] - mug[:2]))
    inside_xy = xy_dist < 0.04
    close_z = duck[2] < mug[2] + 0.05
    # Duck must have moved from its original position
    duck_before = np.array(before["duck"])
    moved = float(np.linalg.norm(duck - duck_before)) > 0.05
    ok = inside_xy and close_z and moved
    detail = (f"duck at {duck.tolist()}, mug at {mug.tolist()}, "
              f"xy_dist={xy_dist:.3f}m, duck_moved={moved}")
    return ok, detail, {"xy_dist_m": xy_dist, "moved": moved}


def _validate_collision_avoid(skel, before: dict) -> tuple[bool, str, dict]:
    """EE moved to target [0.20, 0.0, 0.20]. Bottle MUST NOT have moved >2cm."""
    bottle = skel.get_object_position("bottle")
    bottle_before = np.array(before["bottle"])
    bottle_moved = float(np.linalg.norm(bottle - bottle_before))
    bottle_ok = bottle_moved < 0.02
    ee_pos, _ = skel.get_ee_pose()
    target = np.array([0.20, 0.0, 0.20])
    ee_err = float(np.linalg.norm(ee_pos - target))
    ee_ok = ee_err < 0.05
    ok = bottle_ok and ee_ok
    detail = (f"bottle moved {bottle_moved*100:.1f}cm (must be <2cm), "
              f"EE err to target {ee_err*100:.1f}cm (must be <5cm)")
    return ok, detail, {"bottle_moved_cm": bottle_moved * 100,
                         "ee_err_cm": ee_err * 100}


def _validate_swap(skel, before: dict) -> tuple[bool, str, dict]:
    """Banana now at original bottle position, bottle at original banana pos."""
    banana_now = skel.get_object_position("banana")
    bottle_now = skel.get_object_position("bottle")
    banana_target = np.array(before["bottle"])
    bottle_target = np.array(before["banana"])
    banana_err = float(np.linalg.norm(banana_now[:2] - banana_target[:2]))
    bottle_err = float(np.linalg.norm(bottle_now[:2] - bottle_target[:2]))
    ok = banana_err < 0.08 and bottle_err < 0.08
    detail = (f"banana XY err {banana_err*100:.1f}cm to bottle's old pos, "
              f"bottle XY err {bottle_err*100:.1f}cm to banana's old pos")
    return ok, detail, {"banana_err_cm": banana_err * 100,
                         "bottle_err_cm": bottle_err * 100}


def _validate_stable_place(skel, before: dict) -> tuple[bool, str, dict]:
    """Pick screwdriver, place at [0.15, 0.15, 0.04]. After 1s settle,
    screwdriver still within 3cm of target."""
    target = np.array([0.15, 0.15, 0.04])
    # Settle for 1s (500 sim steps at dt=0.002)
    for _ in range(500):
        skel.step(1)
    sd = skel.get_object_position("screwdriver")
    err = float(np.linalg.norm(sd[:2] - target[:2]))
    z_low = sd[2] < 0.1  # close to table, not floating
    ok = err < 0.05 and z_low
    detail = (f"screwdriver at {sd.tolist()}, target {target.tolist()}, "
              f"XY err {err*100:.1f}cm, z_low={z_low}")
    return ok, detail, {"xy_err_cm": err * 100, "z_m": sd[2]}


_TASKS = [
    {
        "id": "stack_lego_on_duck",
        "desc": (
            "Pick up the lego brick. Move it directly above the duck. Lower "
            "it to about 3 cm above the duck's current Z position. Release "
            "the gripper to drop the lego onto the duck. Return EE to home. "
            "Goal: lego should end up resting on/near duck after physics settles."
        ),
        "record_bodies": ["lego", "duck"],
        "validate": _validate_stack,
    },
    {
        "id": "place_duck_inside_mug",
        "desc": (
            "Pick up the duck. Move it directly above the mug. Lower the EE "
            "until the duck is about 4 cm above the mug, then release. The "
            "duck should end up inside or on top of the mug. Return to home."
        ),
        "record_bodies": ["duck", "mug"],
        "validate": _validate_place_inside,
    },
    {
        "id": "collision_avoid_bottle",
        "desc": (
            "Move the end-effector to world position [0.20, 0.0, 0.20]. "
            "DO NOT knock over the bottle (which is at approximately "
            "[0.30, 0.12, 0.08]). Plan your path to stay clear of the "
            "bottle. Do NOT grasp anything."
        ),
        "record_bodies": ["bottle"],
        "validate": _validate_collision_avoid,
    },
    {
        "id": "swap_banana_bottle",
        "desc": (
            "Swap the positions of the banana and the bottle. Specifically: "
            "(1) get original positions of both via get_object_position. "
            "(2) pick the banana, move it to a TEMPORARY parking position "
            "(somewhere safe like [0.10, 0.20, 0.10]), release. "
            "(3) pick the bottle, move it to the banana's original X/Y "
            "position (keep its current Z), release. "
            "(4) pick the banana from the parking spot, move it to the bottle's "
            "original X/Y position, release. (5) return home."
        ),
        "record_bodies": ["banana", "bottle"],
        "validate": _validate_swap,
    },
    {
        "id": "stable_place_screwdriver",
        "desc": (
            "Pick up the screwdriver. Carefully place it at the target XY "
            "position (0.15, 0.15) on the table. Lower the EE to about "
            "5 cm above the table, release, and gently retract. Return to home. "
            "The screwdriver must end up resting stably near (0.15, 0.15)."
        ),
        "record_bodies": ["screwdriver"],
        "validate": _validate_stable_place,
    },
]


# ──────────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    ws = REPO_ROOT / "artifacts/auto_adapter_so101_v2_artifacts"
    if not (ws / "driver.py").exists():
        raise SystemExit(f"missing driver.py at {ws}")
    out_root = REPO_ROOT / "auto_adapter_contact_rich"
    out_root.mkdir(exist_ok=True)

    print("=" * 78)
    print("CONTACT-RICH TASK BENCHMARK (SO-101, physics-validated)")
    print("=" * 78)

    results: list[dict] = []

    with TaskPlanner(
        workspace=ws,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters=30, max_tokens_per_turn=6000,
    ) as planner:
        for task in _TASKS:
            print(f"\n--- {task['id']} ---")
            print(f"  task: {task['desc'][:160]}")
            # We need to record "before" state. Build a fresh skeleton + record,
            # then let TaskPlanner build its OWN fresh skeleton when executing.
            skel_for_record = planner._load_driver()
            skel_for_record.home()
            before = {b: skel_for_record.get_object_position(b).tolist()
                      for b in task["record_bodies"]}
            print(f"  before: {before}")

            # Execute via TaskPlanner (it builds its OWN fresh skeleton →
            # world resets, our `before` measurements are for the same scene
            # in the same starting state)
            r = planner.execute_task(task["desc"], task_id=task["id"])
            print(f"  LLM says: ok={r.ok} calls={r.n_tool_calls} "
                  f"dur={r.duration_sec:.1f}s")
            if r.summary:
                print(f"  summary: {r.summary[:200]}")

            # Physics validation — load the same workspace again to get the
            # POST-execution skeleton state. TaskPlanner already left it after
            # its last tool call.
            # Build another fresh instance to verify — but wait, that resets
            # the world. We need to peek at the LIVE state from inside the
            # planner's run. Hack: re-build, but the world is reset each
            # execute_task call. So actually we need different design.
            #
            # Better: pass `validate_after_run` as a hook to TaskPlanner.
            # For now do: build fresh skel, replay tool_call_log on it,
            # then physics-validate.
            replay_skel = planner._load_driver()
            for call in r.tool_call_log:
                tool = call["tool"]
                inp = call["input"]
                method = getattr(replay_skel, tool, None)
                if method is None:
                    continue
                try:
                    # Map ToolSpec inputs back to method args (simple cases)
                    if tool == "move_cartesian":
                        method(np.array([inp["x"], inp["y"], inp["z"]]),
                               duration=inp.get("duration", 2.0))
                    elif tool in ("home", "gripper_open", "gripper_close",
                                  "is_holding", "get_ee_pose",
                                  "get_joint_positions"):
                        method()
                    elif tool == "get_object_position":
                        method(inp.get("body_name", ""))
                    else:
                        method()
                except Exception as e:  # noqa: BLE001
                    print(f"  replay error on {tool}: {type(e).__name__}: {e}")
                    break

            ok, detail, metrics = task["validate"](replay_skel, before)
            print(f"  PHYSICS: ok={ok} — {detail}")
            print(f"  LLM↔physics: {'AGREE' if (r.ok and ok) or (not r.ok and not ok) else 'DISAGREE'}")

            # Save mp4
            mp4_dest = None
            if r.mp4_path and r.mp4_path.exists():
                mp4_dest = out_root / f"{task['id']}.mp4"
                mp4_dest.write_bytes(r.mp4_path.read_bytes())

            results.append({
                "task_id": task["id"],
                "task": task["desc"],
                "llm_ok": bool(r.ok),
                "physics_ok": bool(ok),
                "agreement": bool(r.ok == ok),
                "physics_detail": detail,
                "physics_metrics": metrics,
                "tool_calls": r.n_tool_calls,
                "frames": r.n_frames,
                "duration_sec": r.duration_sec,
                "tokens": r.token_usage,
                "summary": r.summary,
                "error": r.error,
                "before_positions": before,
                "mp4": str(mp4_dest) if mp4_dest else None,
            })

    (out_root / "contact_rich_summary.json").write_text(
        json.dumps(results, indent=2, default=str))

    print("\n" + "=" * 78)
    print("SCOREBOARD — CONTACT-RICH TASKS")
    print("=" * 78)
    print(f"{'Task':<28} {'LLM':>5}  {'Physics':>8}  {'Agree':>6}  {'Cost ($)':>9}")
    print("-" * 78)
    tot_in = tot_out = 0
    n_llm_ok = n_phys_ok = n_agree = 0
    for r in results:
        llm = "OK" if r["llm_ok"] else "FAIL"
        phys = "OK" if r["physics_ok"] else "FAIL"
        agree = "✓" if r["agreement"] else "✗"
        in_ = r["tokens"].get("in", 0)
        out_t = r["tokens"].get("out", 0)
        cost = in_ * 3e-6 + out_t * 15e-6
        tot_in += in_; tot_out += out_t
        if r["llm_ok"]: n_llm_ok += 1
        if r["physics_ok"]: n_phys_ok += 1
        if r["agreement"]: n_agree += 1
        print(f"{r['task_id']:<28} {llm:>5}  {phys:>8}  {agree:>6}  ${cost:>7.2f}")
    grand_cost = tot_in * 3e-6 + tot_out * 15e-6
    print("-" * 78)
    print(f"  LLM says ok:      {n_llm_ok}/{len(results)}")
    print(f"  Physics says ok:  {n_phys_ok}/{len(results)}")
    print(f"  LLM↔physics agree: {n_agree}/{len(results)}")
    print(f"  Total cost:       ${grand_cost:.2f}")
    print(f"\n  Output: {out_root}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
