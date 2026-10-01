# SPDX-License-Identifier: Apache-2.0
"""Task-level generalization showcase: run distinct NL tasks on each robot.

Validates the Layer 1 TaskPlanner by:
  - Loading each robot's agent-generated driver.py
  - Running 2-3 DISTINCT tasks per robot (different from VALIDATE / DEMO behaviors)
  - Recording one mp4 per task

This is what proves "different tasks on same robot work", not just
"same task on different robots". Output is a per-robot summary + per-task mp4.

Costs roughly $1-3 in tokens (Sonnet 4.5) — each task is a small ReactLoop
(typically 5-15 tool calls).
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
# Tasks per robot
# ──────────────────────────────────────────────────────────────────────────


_TASKS: dict[str, list[str]] = {
    "artifacts/auto_adapter_so101_v2_artifacts": [
        "Pick up the banana and lift it 15 centimeters into the air, then hold it there.",
        "Move the end-effector to the position of the mug, then return to home.",
        "Trace a small horizontal square: move +5cm in X, then +5cm in Y, then -5cm in X, then -5cm in Y. Report the EE position at each corner.",
    ],
    "auto_adapter_piper_artifacts": [
        "Wave hello: move the end-effector up 5 cm and back down 3 times.",
        "Trace a small square in the XY plane around the current EE position (each side ~5 cm).",
    ],
    "auto_adapter_franka_artifacts": [
        "Starting from home, move the end-effector through 4 corners of a small square in the XY plane around the current EE position (each side ~5 cm), then return to home.",
        "Open the gripper, wait, then close it. Report whether anything was grasped.",
    ],
    "auto_adapter_go2_artifacts": [
        "Stand up, hold the standing pose for a moment, then sit back down.",
        "Sit, stand, sit, stand — alternate twice. Keep each phase short (under 1.5 seconds).",
    ],
}


def main() -> None:
    print("=" * 72)
    print("Layer 1 TaskPlanner — task-level generalization showcase")
    print("=" * 72)

    summary_rows: list[dict] = []
    showcase_root = REPO_ROOT / "auto_adapter_task_showcase"
    showcase_root.mkdir(exist_ok=True)

    for robot_dir, tasks in _TASKS.items():
        ws = REPO_ROOT / robot_dir
        if not (ws / "driver.py").exists():
            print(f"\n[skip] {robot_dir} (no driver.py)")
            continue

        print(f"\n{'='*72}")
        print(f"ROBOT: {robot_dir}")
        print(f"{'='*72}")

        # Set up a per-robot showcase output dir under the showcase root
        robot_out = showcase_root / robot_dir.replace("auto_adapter_", "").replace("_artifacts", "")
        robot_out.mkdir(exist_ok=True)
        robot_summary: list[dict] = []

        with TaskPlanner(
            workspace=ws,
            bedrock_model=os.environ.get(
                "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
            ),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            max_iters=20,
            max_tokens_per_turn=4000,
        ) as planner:
            for i, task in enumerate(tasks):
                task_id = f"task_{i+1:02d}"
                print(f"\n--- {task_id} ---")
                print(f"  task: {task}")
                result = planner.execute_task(task, task_id=task_id)
                print(f"  ok={result.ok} frames={result.n_frames} "
                      f"calls={result.n_tool_calls} dur={result.duration_sec:.1f}s "
                      f"tok={result.token_usage}")
                if result.summary:
                    print(f"  summary: {result.summary[:240]!r}")
                if result.error:
                    print(f"  error: {result.error}")
                if result.mp4_path:
                    # Copy mp4 into the showcase output dir
                    showcase_mp4 = robot_out / f"{task_id}.mp4"
                    showcase_mp4.write_bytes(result.mp4_path.read_bytes())
                    print(f"  mp4: {showcase_mp4} ({showcase_mp4.stat().st_size/1024:.1f} KB)")

                row = {
                    "task_id": task_id,
                    "task": task,
                    "ok": result.ok,
                    "frames": result.n_frames,
                    "tool_calls": result.n_tool_calls,
                    "duration_sec": result.duration_sec,
                    "tokens": result.token_usage,
                    "summary": result.summary,
                    "error": result.error,
                    "mp4": str(showcase_mp4) if result.mp4_path else None,
                    "tool_call_log_first_5": result.tool_call_log[:5],
                }
                robot_summary.append(row)
                summary_rows.append({"robot": robot_dir, **row})

        (robot_out / "tasks_summary.json").write_text(
            json.dumps(robot_summary, indent=2, default=str)
        )

    # Top-level showcase summary
    (showcase_root / "showcase_summary.json").write_text(
        json.dumps(summary_rows, indent=2, default=str)
    )

    print(f"\n{'='*72}")
    print(f"SHOWCASE COMPLETE")
    print(f"{'='*72}")
    print(f"  output dir: {showcase_root}")
    n_ok = sum(1 for r in summary_rows if r["ok"])
    print(f"  tasks: {len(summary_rows)} total, {n_ok} ok, {len(summary_rows) - n_ok} failed")
    by_robot: dict[str, list] = {}
    for r in summary_rows:
        by_robot.setdefault(r["robot"], []).append(r)
    for robot, rows in by_robot.items():
        ok = sum(1 for r in rows if r["ok"])
        print(f"  {robot}: {ok}/{len(rows)} ok")
    print(f"  see {showcase_root}/*/<task_id>.mp4 for videos")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
