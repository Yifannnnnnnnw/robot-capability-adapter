# SPDX-License-Identifier: Apache-2.0
"""From-scratch drivers × task suite — does task-level generalization extend
to drivers the agent wrote from scratch (no skeleton library)?

Setup:
  - Use the existing TaskPlanner (no changes needed — class dispatch handles
    the agent's `Robot` class via instance introspection now).
  - Each from-scratch driver has a slightly different API surface — we pick
    tasks that match what each driver exposes:
      * SO-101 / Franka (arm, has move_cartesian / get_ee_pose / gripper):
        5 arm tasks
      * Go2 (quadruped, has stand_up / sit / walk_forward):
        5 quadruped tasks
  - Same task descriptions as the existing standard task suite — direct
    comparison to framework-driver numbers we already have.

Output:
  auto_adapter_from_scratch_tasks/
    so101/task_*.mp4
    franka/task_*.mp4
    go2/task_*.mp4
    summary.json

Cost: ~$3-5 (5 tasks × 3 robots × ~$0.15 per task)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner


_ARM_TASKS = [
    ("move_x_plus_5cm",
     "Move the end-effector +5 cm in X from its current position, hold "
     "briefly, then return to home."),
    ("move_z_plus_5cm",
     "Move the end-effector +5 cm in Z (upward), hold briefly, then return "
     "to home."),
    ("open_close_gripper",
     "Open the gripper, wait, then close the gripper. Report whether anything "
     "was grasped."),
    ("trace_square_xy",
     "Trace a small 4 cm square in the XY plane starting from the current "
     "EE position, returning to start."),
    ("unreachable_detect",
     "Try to move the EE to world position [3.0, 3.0, 3.0] — way outside "
     "any arm's workspace. If unreachable, return to home and report the "
     "failure. Do NOT retry."),
]

_QUAD_TASKS = [
    ("stand_up",
     "Stand up from the current pose."),
    ("sit",
     "Sit down to a folded pose."),
    ("stand_sit_stand",
     "Stand, then sit, then stand again. Use duration 1.5 seconds for each."),
    ("report_height_pose",
     "Report the current torso pose and body height."),
    ("walk_1s",
     "Walk forward for 1 second at speed 0.15. Report the forward "
     "displacement after."),
]


_TARGETS = [
    ("so101",  REPO_ROOT / "artifacts/auto_adapter_from_scratch_so101_artifacts",  _ARM_TASKS),
    ("franka", REPO_ROOT / "artifacts/auto_adapter_from_scratch_franka_artifacts", _ARM_TASKS),
    ("go2",    REPO_ROOT / "artifacts/auto_adapter_from_scratch_go2_artifacts",    _QUAD_TASKS),
]


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_from_scratch_tasks"
    out_root.mkdir(exist_ok=True)
    all_results: list[dict] = []

    print("=" * 72)
    print("FROM-SCRATCH DRIVERS × TASK SUITE")
    print("=" * 72)
    print(
        "Same hard tasks the framework driver passed at 100%. Now using "
        "drivers the agent wrote from zero — no auto_adapter.skeletons imports."
    )
    print()

    for rid, ws, tasks in _TARGETS:
        # Critical: the from-scratch drivers reference 'mjcf.xml' relative to
        # the workspace; that file must be a SYMLINK to the real MJCF (was
        # broken earlier by shutil.copytree — re-symlinked in artifacts now).
        if not (ws / "driver_from_scratch.py").exists():
            print(f"[skip] {rid}: no driver_from_scratch.py at {ws}")
            continue
        mjcf_link = ws / "mjcf.xml"
        if not (mjcf_link.exists() or mjcf_link.is_symlink()):
            print(f"[skip] {rid}: mjcf.xml symlink missing at {ws}")
            continue

        print(f"\n{'='*72}")
        print(f"ROBOT: {rid}  (driver: {ws.name}/driver_from_scratch.py)")
        print(f"{'='*72}")
        robot_out = out_root / rid
        robot_out.mkdir(exist_ok=True)

        # Patch TaskPlanner to load `driver_from_scratch` instead of `driver`.
        # Easiest: temporary rename + restore.
        link_target = ws / "driver.py"
        had_existing = link_target.exists() or link_target.is_symlink()
        if had_existing:
            link_target.rename(ws / "driver.py.bak")
        try:
            link_target.symlink_to("driver_from_scratch.py")

            with TaskPlanner(
                workspace=ws,
                bedrock_model=os.environ.get(
                    "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
                max_iters=25,
                max_tokens_per_turn=5000,
            ) as planner:
                for task_id, task_desc in tasks:
                    print(f"\n--- {task_id} ---")
                    print(f"  task: {task_desc[:140]}")
                    try:
                        r = planner.execute_task(
                            task_desc,
                            task_id=f"fs_{task_id}",
                        )
                        print(f"  ok={r.ok} calls={r.n_tool_calls} "
                              f"frames={r.n_frames} dur={r.duration_sec:.1f}s "
                              f"tok={r.token_usage}")
                        print(f"  summary: {(r.summary or '')[:200]}")
                        if r.error:
                            print(f"  error: {r.error}")
                        if r.mp4_path and r.mp4_path.exists():
                            dest = robot_out / f"{task_id}.mp4"
                            dest.write_bytes(r.mp4_path.read_bytes())
                            print(f"  mp4: {dest.name} ({dest.stat().st_size/1024:.1f} KB)")
                        all_results.append({
                            "robot": rid, "task_id": task_id, "task": task_desc,
                            "ok": r.ok,
                            "tool_calls": r.n_tool_calls,
                            "frames": r.n_frames,
                            "duration_sec": r.duration_sec,
                            "tokens": r.token_usage,
                            "summary": r.summary,
                            "error": r.error,
                            "tool_call_log_first_5": r.tool_call_log[:5],
                        })
                    except Exception as e:  # noqa: BLE001
                        print(f"  CRASH: {type(e).__name__}: {e}")
                        all_results.append({
                            "robot": rid, "task_id": task_id, "task": task_desc,
                            "ok": False, "tool_calls": 0, "frames": 0,
                            "duration_sec": 0.0, "tokens": {},
                            "summary": "",
                            "error": f"crash: {type(e).__name__}: {e}",
                        })
        finally:
            if link_target.is_symlink() or link_target.exists():
                link_target.unlink(missing_ok=True)
            if (ws / "driver.py.bak").exists():
                (ws / "driver.py.bak").rename(link_target)

    (out_root / "summary.json").write_text(
        json.dumps(all_results, indent=2, default=str))

    print(f"\n{'='*72}\nSCOREBOARD — From-scratch drivers × task suite\n{'='*72}")
    by_robot: dict[str, list[dict]] = {}
    for r in all_results:
        by_robot.setdefault(r["robot"], []).append(r)
    n_total = len(all_results); n_ok = sum(1 for r in all_results if r["ok"])
    print(f"  Overall: {n_ok}/{n_total} = {n_ok/max(n_total,1)*100:.0f}%")
    print()
    grand_in = grand_out = 0
    for rid, rows in by_robot.items():
        ok = sum(1 for r in rows if r["ok"])
        avg_calls = sum(r["tool_calls"] for r in rows) / max(len(rows), 1)
        tot_in = sum(r["tokens"].get("in", 0) for r in rows)
        tot_out = sum(r["tokens"].get("out", 0) for r in rows)
        grand_in += tot_in; grand_out += tot_out
        cost = tot_in * 3e-6 + tot_out * 15e-6
        print(f"  {rid:8s}: {ok}/{len(rows)} pass   avg_calls={avg_calls:.1f}   "
              f"tot ${cost:.2f}")
    grand_cost = grand_in * 3e-6 + grand_out * 15e-6
    print(f"\n  Total cost: ${grand_cost:.2f}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
