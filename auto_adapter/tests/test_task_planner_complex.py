# SPDX-License-Identifier: Apache-2.0
"""Complex multi-step tasks — proves the planner actually executes sustained
robot behavior, not just one IK jab + render.

Each task:
  - has multiple distinct phases (move-grasp-move-release, etc.)
  - has at least one DECISION point (check is_holding, branch)
  - takes 5-15+ seconds of REAL sim time
  - produces an mp4 you can scrub through and see continuous motion

Compared to test_task_planner_showcase.py, these tasks are designed to
stress the planner — not just "wave hello once" but "pick A, move A to
position of B, release, return home" type sequences.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner


# 5 complex tasks: 3 SO-101 (has graspable bodies, can do pick-and-place),
# 1 Franka (multi-waypoint), 1 Go2 (multi-phase locomotion sequence).

_TASKS: list[tuple[str, str, str]] = [
    # (robot_dir, task_id, task_description)
    (
        "artifacts/auto_adapter_so101_v2_artifacts",
        "pick_and_place_banana_at_mug",
        "Pick up the banana, then carry it over to where the mug is and "
        "release it just above the mug (don't drop it ON the mug — release it "
        "about 5 cm above the mug). Finally return the arm to home. "
        "At each step, report what you observed.",
    ),
    (
        "artifacts/auto_adapter_so101_v2_artifacts",
        "stack_2_objects",
        "Pick up the lego, lift it 10 cm into the air, then move it directly "
        "above the position where the duck is sitting, then descend to about "
        "3 cm above the duck and release. Return home.",
    ),
    (
        "artifacts/auto_adapter_so101_v2_artifacts",
        "sort_inspect",
        "Visit each of the 6 graspable objects in turn — banana, mug, bottle, "
        "screwdriver, duck, lego. For each: move the EE to a position 5 cm "
        "above the object, briefly hover, and report whether the gripper is "
        "currently holding anything (it shouldn't be). Finally return home.",
    ),
    (
        "auto_adapter_franka_artifacts",
        "draw_letter_L",
        "Starting from home, draw the letter L in the air by moving the EE "
        "through 3 waypoints: (a) move +15 cm down in Z to start the vertical "
        "stroke, (b) hold there, (c) move +10 cm in X to complete the "
        "horizontal stroke of the L, (d) hold, then return to home. "
        "Report the EE position at each corner.",
    ),
    (
        "auto_adapter_go2_artifacts",
        "stand_hold_3s_then_sit",
        "Stand up from the current pose. Once standing, hold the stance for a "
        "FULL 3 seconds (use the duration argument). Then sit back down "
        "slowly (sit duration 2 seconds). Report the body height at each "
        "phase: before stand, after stand+hold, after sit.",
    ),
]


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_task_complex"
    out_root.mkdir(exist_ok=True)
    all_results: list[dict] = []

    # Group tasks per robot so we only construct one TaskPlanner per workspace
    by_robot: dict[str, list[tuple[str, str]]] = {}
    for robot_dir, task_id, task_desc in _TASKS:
        by_robot.setdefault(robot_dir, []).append((task_id, task_desc))

    for robot_dir, tasks in by_robot.items():
        ws = REPO_ROOT / robot_dir
        if not (ws / "driver.py").exists():
            print(f"[skip] {robot_dir} (no driver.py)")
            continue

        print(f"\n{'='*72}")
        print(f"ROBOT: {robot_dir}")
        print(f"{'='*72}")
        robot_short = robot_dir.replace("auto_adapter_", "").replace("_artifacts", "")
        robot_out = out_root / robot_short
        robot_out.mkdir(exist_ok=True)

        with TaskPlanner(
            workspace=ws,
            bedrock_model=os.environ.get(
                "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
            ),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            max_iters=30,           # generous for multi-step plans
            max_tokens_per_turn=6000,
        ) as planner:
            for task_id, task_desc in tasks:
                print(f"\n--- {task_id} ---")
                print(f"  task: {task_desc[:160]}{'…' if len(task_desc) > 160 else ''}")
                result = planner.execute_task(task_desc, task_id=task_id)
                print(f"  ok={result.ok} frames={result.n_frames} "
                      f"calls={result.n_tool_calls} dur={result.duration_sec:.1f}s "
                      f"tok={result.token_usage}")
                if result.summary:
                    print(f"  summary: {result.summary[:300]!r}")
                if result.error:
                    print(f"  error: {result.error}")
                if result.mp4_path and result.mp4_path.exists():
                    dest = robot_out / f"{task_id}.mp4"
                    dest.write_bytes(result.mp4_path.read_bytes())
                    print(f"  mp4: {dest} ({dest.stat().st_size/1024:.1f} KB)")
                    # Approximate sim-time covered by the video
                    sim_time_s = (result.n_frames * 10) * 0.002  # capture_every=10 × sim_dt=0.002
                    print(f"  approx sim time covered: {sim_time_s:.1f} s "
                          f"({result.n_frames} frames @ 30fps = {result.n_frames/30:.1f}s video)")

                all_results.append({
                    "robot": robot_dir,
                    "task_id": task_id,
                    "task": task_desc,
                    "ok": result.ok,
                    "frames": result.n_frames,
                    "tool_calls": result.n_tool_calls,
                    "duration_sec": result.duration_sec,
                    "tokens": result.token_usage,
                    "summary": result.summary,
                    "error": result.error,
                    "tool_call_log": result.tool_call_log,
                    "mp4": str(robot_out / f"{task_id}.mp4")
                            if result.mp4_path else None,
                })

    (out_root / "complex_summary.json").write_text(
        json.dumps(all_results, indent=2, default=str)
    )

    print(f"\n{'='*72}")
    print(f"COMPLEX TASKS COMPLETE")
    print(f"{'='*72}")
    print(f"  output dir: {out_root}")
    n_ok = sum(1 for r in all_results if r["ok"])
    print(f"  {n_ok}/{len(all_results)} tasks ok")
    for r in all_results:
        flag = "OK" if r["ok"] else "FAIL"
        sim_s = (r["frames"] * 10) * 0.002
        print(f"  [{flag}] {r['robot'].split('_')[2]}/{r['task_id']}: "
              f"{r['tool_calls']} calls, {r['frames']} frames "
              f"(~{sim_s:.1f}s sim), tok={r['tokens']}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
