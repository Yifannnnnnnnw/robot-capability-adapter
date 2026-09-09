# SPDX-License-Identifier: Apache-2.0
"""Hard task benchmark — stress-test the TaskPlanner.

The earlier task suite scored 100% on easy tasks (move 5cm, trace square,
open/close gripper). These tasks are designed to be hard enough that we
EXPECT some failures — only by finding the breaking point can we trust
the success numbers when we report them.

Task categories:

  1. CONDITIONAL — branch on observed state
     "If the banana is to the left of the mug, pick the banana; otherwise pick the mug."

  2. MULTI-OBJECT — coordinate over several scene bodies
     "Move the EE above each graspable body, in order from leftmost to rightmost."

  3. FAILURE RECOVERY — agent must notice + retry
     "Try to grasp the banana. If the grasp fails (is_holding=False), open
      the gripper, descend an extra 2 cm, and try again. Report success/fail."

  4. NUMERIC REASONING — agent must compute geometry
     "Pick the banana, move it to the midpoint between the original positions
      of mug and bottle, and release."

  5. LONG-HORIZON — many tool calls, no shortcut
     "Visit all 6 graspable bodies in sequence. For each, lift it 5 cm, hold
      briefly, then release back where it was. Visit them in alphabetical
      order. Final report: how many you successfully grasped."

  6. UNREACHABLE / NEGATIVE — task is impossible; agent must detect + report
     "Move the EE to position [2.0, 2.0, 2.0] — that's far outside the
      workspace. If unreachable, return to home and report so."

Costs ~$0.30-0.50 per task on a complex one (more iters, more tokens).
6 tasks × 4 arm-robots = ~$10 total (we skip quadrupeds for these tests).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner


# ──────────────────────────────────────────────────────────────────────────
# Robot drivers to test (arms only — quadruped task surface is smaller)
# ──────────────────────────────────────────────────────────────────────────


ROBOT_DIRS = [
    "artifacts/auto_adapter_so101_v2_artifacts",   # 5-DOF, weld grasp, has graspable bodies
    "auto_adapter_piper_artifacts",      # 6-DOF, contact grasp, no graspables
    "auto_adapter_franka_artifacts",     # 7-DOF, ee_body, contact grasp
    "auto_adapter_ur5e_artifacts",       # 6-DOF, no gripper
    "auto_adapter_kuka_iiwa14_artifacts", # 7-DOF, no gripper
]


# ──────────────────────────────────────────────────────────────────────────
# Hard tasks. The SO-101 has real graspable bodies — its task set is
# meatier. Gripperless arms get a constrained variant.
# ──────────────────────────────────────────────────────────────────────────


_TASKS_WITH_GRASP = [
    (
        "conditional_pick",
        "Look at the current positions of the banana and the mug. "
        "If the banana's X-coordinate is LESS than the mug's X-coordinate "
        "(i.e. banana is more to the left in the world frame), pick up the "
        "banana and lift it 10 cm. Otherwise pick up the mug and lift it 10 "
        "cm. Report which one you picked and why.",
    ),
    (
        "multi_object_sweep",
        "Observe the positions of banana, mug, bottle, screwdriver, duck, "
        "and lego. Then visit each one (hover 5 cm above the body) IN ORDER "
        "OF INCREASING X-COORDINATE — leftmost first, rightmost last. "
        "Don't grasp anything. After visiting all 6, return home. "
        "Report the order you visited them in.",
    ),
    (
        "failure_recovery",
        "Try to grasp the banana with the following retry logic: (1) approach "
        "5 cm above banana, (2) descend to 2 cm above, (3) close gripper, "
        "(4) check is_holding. If NOT holding, open gripper, descend an extra "
        "1 cm (now 1 cm above banana), close again, check again. If STILL "
        "not holding, descend 1 cm more (touching), close, check. Report "
        "how many attempts it took and the final outcome.",
    ),
    (
        "numeric_midpoint",
        "Get the positions of the mug and the bottle. Compute the midpoint "
        "(average X, average Y, average Z + 5 cm). Move the EE to that "
        "midpoint and report whether you reached it. Don't grasp anything.",
    ),
    (
        "long_horizon_alpha_sweep",
        "Visit all 6 graspable bodies (banana, bottle, duck, lego, mug, "
        "screwdriver) in ALPHABETICAL ORDER. For each: approach 5 cm above, "
        "close gripper, lift 5 cm, hold briefly, lower 5 cm, open gripper, "
        "move on to next. Final report: how many bodies you successfully "
        "grasped (is_holding was True at lift) and total tool calls.",
    ),
    (
        "unreachable_detect",
        "Try to move the end-effector to world position [2.0, 2.0, 2.0] — "
        "that's far outside the typical workspace of any tabletop arm. "
        "If the move fails or is unreachable, return to home and report "
        "the failure. Do NOT keep retrying.",
    ),
]


_TASKS_GRIPPERLESS = [
    (
        "conditional_pose_choice",
        "Get the current EE pose. If the EE's Z-coordinate is GREATER than "
        "0.5 meters, move the EE 10 cm down (decrease Z by 0.10). Otherwise "
        "move 10 cm up. Report which way you moved and the final Z.",
    ),
    (
        "joint_waypoint_sequence",
        "Read the current joint positions. Plan three target poses by adding "
        "+0.1 rad to one joint at a time (joint 0, then joint 1, then joint "
        "2). For each target pose, set joints via move_cartesian (compute "
        "what Cartesian goal that would correspond to via small EE offsets "
        "from current). Just describe what you would do — DO NOT actually "
        "modify joints directly; use move_cartesian with small Cartesian "
        "offsets. Then return home.",
    ),
    (
        "trace_circle",
        "Trace a small CIRCLE (not square) in the XY plane around the "
        "current EE position. Use 8 waypoints evenly spaced around a circle "
        "of radius 3 cm. Return to start after completing the circle.",
    ),
    (
        "midpoint_3d",
        "Compute the midpoint between the current EE position and the "
        "position offset by (+0.05, +0.05, +0.05). Move there, then move "
        "to the offset target, then back to start. Report the EE position "
        "at each waypoint.",
    ),
    (
        "long_horizon_grid",
        "Visit 6 grid points in the XY plane (3 columns × 2 rows) around "
        "the current EE position. Spacing 4 cm. Visit them row-major "
        "(top-left to bottom-right). For each point, report the EE Z. "
        "After the 6 points, return home.",
    ),
    (
        "unreachable_detect",
        "Try to move the EE to world position [3.0, 3.0, 3.0] — way outside "
        "any arm's workspace. If the move fails or IK is unreachable, "
        "return to home and report the failure. Do NOT retry.",
    ),
]


def _has_graspable_objects(driver_dir: Path) -> bool:
    """Heuristic: SO-101 driver mentions weld_graspable_bodies with content."""
    text = (driver_dir / "driver.py").read_text()
    if "weld_graspable_bodies=" not in text:
        return False
    # Check there's an actual non-empty list
    for line in text.splitlines():
        if "weld_graspable_bodies=" in line and "[" in line and "'" in line:
            return True
        if "weld_graspable_bodies=[" in line:
            return True
    return False


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_hard_tasks"
    out_root.mkdir(exist_ok=True)
    all_results: list[dict] = []

    for robot_dir in ROBOT_DIRS:
        ws = REPO_ROOT / robot_dir
        if not (ws / "driver.py").exists():
            print(f"[skip] {robot_dir} (no driver.py)")
            continue

        has_grasp = _has_graspable_objects(ws)
        tasks = _TASKS_WITH_GRASP if has_grasp else _TASKS_GRIPPERLESS
        rid = robot_dir.replace("auto_adapter_", "").replace("_artifacts", "")
        robot_out = out_root / rid
        robot_out.mkdir(exist_ok=True)

        print(f"\n{'='*72}")
        print(f"HARD TASKS: {rid}  ({'graspable' if has_grasp else 'no-graspables'})")
        print(f"{'='*72}")

        with TaskPlanner(
            workspace=ws,
            bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            max_iters=30,                # generous for hard tasks
            max_tokens_per_turn=6000,
        ) as planner:
            for task_id, task_desc in tasks:
                print(f"\n--- {task_id} ---")
                print(f"  task: {task_desc[:160]}{'…' if len(task_desc) > 160 else ''}")
                try:
                    r = planner.execute_task(task_desc, task_id=task_id)
                    print(f"  ok={r.ok} calls={r.n_tool_calls} "
                          f"frames={r.n_frames} dur={r.duration_sec:.1f}s "
                          f"tok={r.token_usage}")
                    if r.summary:
                        print(f"  summary: {r.summary[:240]!r}")
                    if r.error:
                        print(f"  error: {r.error}")
                    if r.mp4_path and r.mp4_path.exists():
                        dest = robot_out / f"{task_id}.mp4"
                        dest.write_bytes(r.mp4_path.read_bytes())

                    all_results.append({
                        "robot": rid,
                        "task_id": task_id,
                        "task": task_desc,
                        "ok": r.ok,
                        "tool_calls": r.n_tool_calls,
                        "frames": r.n_frames,
                        "duration_sec": r.duration_sec,
                        "tokens": r.token_usage,
                        "summary": r.summary,
                        "error": r.error,
                        "tool_call_log": r.tool_call_log,
                    })
                except Exception as e:  # noqa: BLE001
                    print(f"  CRASH: {type(e).__name__}: {e}")
                    all_results.append({
                        "robot": rid, "task_id": task_id, "task": task_desc,
                        "ok": False, "tool_calls": 0, "frames": 0,
                        "duration_sec": 0.0, "tokens": {},
                        "summary": "", "error": f"crash: {type(e).__name__}: {e}",
                    })

    (out_root / "hard_summary.json").write_text(
        json.dumps(all_results, indent=2, default=str)
    )

    # Final scoreboard
    by_robot: dict[str, list[dict]] = {}
    for r in all_results:
        by_robot.setdefault(r["robot"], []).append(r)

    print(f"\n{'='*72}\nHARD TASK SCOREBOARD\n{'='*72}")
    print(f"{'Robot':<20}{'OK':>8}  {'AvgCalls':>10}  {'AvgTok(in/out)':>20}  {'Cost ($)':>10}")
    grand_in = grand_out = 0
    for rid, rows in by_robot.items():
        n_ok = sum(1 for r in rows if r["ok"])
        avg_calls = sum(r["tool_calls"] for r in rows) / max(len(rows), 1)
        tot_in = sum(r["tokens"].get("in", 0) for r in rows)
        tot_out = sum(r["tokens"].get("out", 0) for r in rows)
        avg_in = tot_in / max(len(rows), 1)
        avg_out = tot_out / max(len(rows), 1)
        grand_in += tot_in
        grand_out += tot_out
        cost = tot_in * 3e-6 + tot_out * 15e-6
        print(f"{rid:<20}{n_ok}/{len(rows):>6}  {avg_calls:>10.1f}  "
              f"{avg_in:>9.0f}/{avg_out:<9.0f}  ${cost:>8.2f}")

    n_total = len(all_results)
    n_ok = sum(1 for r in all_results if r["ok"])
    grand_cost = grand_in * 3e-6 + grand_out * 15e-6
    print(f"\n  Total: {n_ok}/{n_total} = {n_ok/max(n_total,1)*100:.0f}%   "
          f"${grand_cost:.2f} total cost")
    print(f"\n  Output: {out_root}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
