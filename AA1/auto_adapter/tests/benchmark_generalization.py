# SPDX-License-Identifier: Apache-2.0
"""Cross-robot generalization benchmark.

Two-part evaluation:

PART A — PIPELINE BENCHMARK
  Run the full SelfAssemble 5-phase pipeline on N new robots (whose drivers
  we don't already have). Record which phase passed for each. Save artifacts.

PART B — TASK BENCHMARK
  Run a standardized 5-task suite on the agent-generated drivers for ALL
  covered robots (the 4 we already had + new ones from Part A). Record
  per-task success / tool calls / frames / tokens.

Output:
  auto_adapter_benchmark/benchmark_summary.json — full results
  auto_adapter_benchmark/SCOREBOARD.md          — human-readable table

Cost budget: ~$15-20 (pipeline gen × 4 new robots + task suite × 8 robots).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter import SelfAssemble, SelfAssembleConfig
from auto_adapter.agent.task_planner import TaskPlanner


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────

MJCF_ROOT = REPO_ROOT / "assets" / "mjcf"

# Robots to RUN the full pipeline on (Part A). Excludes the 4 we already
# have drivers for in auto_adapter_*_artifacts/.
PIPELINE_ROBOTS: list[dict] = [
    {
        "robot_id": "ur5e",
        "mjcf": MJCF_ROOT / "universal_robots_ur5e" / "scene.xml",
        "expected_class": "arm",
        "notes": "6-DOF, no gripper in base scene → tests NoOpGraspBackend",
    },
    {
        "robot_id": "kuka_iiwa14",
        "mjcf": MJCF_ROOT / "kuka_iiwa_14" / "scene.xml",
        "expected_class": "arm",
        "notes": "7-DOF, no gripper",
    },
    {
        "robot_id": "unitree_a1",
        "mjcf": MJCF_ROOT / "unitree_a1" / "scene.xml",
        "expected_class": "quadruped",
        "notes": "12-DOF quadruped, different size from Go2",
    },
    {
        "robot_id": "anymal_c",
        "mjcf": MJCF_ROOT / "anybotics_anymal_c" / "scene.xml",
        "expected_class": "quadruped",
        "notes": "12-DOF quadruped, LF/LH/RF/RH naming (NOT FL/FR/RL/RR)",
    },
]

# Robots to RUN the task suite on (Part B). Includes both pre-built drivers
# from prior runs AND the newly-built ones from Part A.
EXISTING_DRIVER_DIRS: list[dict] = [
    {"id": "so101_v2", "dir": REPO_ROOT / "artifacts/auto_adapter_so101_v2_artifacts",
     "mjcf_link": MJCF_ROOT.parent / "so101_mujoco.xml"},
    {"id": "piper", "dir": REPO_ROOT / "auto_adapter_piper_artifacts",
     "mjcf_link": MJCF_ROOT / "piper" / "scene.xml"},
    {"id": "franka", "dir": REPO_ROOT / "auto_adapter_franka_artifacts",
     "mjcf_link": MJCF_ROOT / "franka_panda" / "scene.xml"},
    {"id": "go2", "dir": REPO_ROOT / "auto_adapter_go2_artifacts",
     "mjcf_link": MJCF_ROOT / "go2" / "go2_scene.xml"},
]

# Task suites
ARM_TASKS = [
    "Move the end-effector +5 cm in X from its current position, hold briefly, then return to home.",
    "Move the end-effector +5 cm in Z (upward), hold briefly, then return to home.",
    "Open the gripper, wait, then close the gripper. Report if anything was grasped.",
    "Trace a small 4 cm square in the XY plane starting from the current EE position, returning to start.",
    "Report the current EE pose, joint positions, and is_holding status.",
]

QUADRUPED_TASKS = [
    "Stand up from the current pose.",
    "Sit down to a folded pose.",
    "Stand, then sit, then stand again. Use durations of 1.5 seconds each.",
    "Report the current torso pose and body height.",
    "Walk forward for 1 second at speed 0.15. Report the forward displacement after.",
]


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class PipelineRow:
    robot_id: str
    mjcf: str
    expected_class: str
    notes: str
    workspace: Optional[str] = None
    phase_results: dict = field(default_factory=dict)
    pipeline_ok: bool = False
    total_tokens: dict = field(default_factory=dict)
    total_duration_sec: float = 0.0
    error: Optional[str] = None


@dataclass
class TaskRow:
    robot_id: str
    task_id: int
    task_description: str
    ok: bool
    tool_calls: int
    frames: int
    duration_sec: float
    tokens: dict
    summary: str
    error: Optional[str]
    mp4_path: Optional[str]


# ──────────────────────────────────────────────────────────────────────────
# Part A — Pipeline benchmark
# ──────────────────────────────────────────────────────────────────────────


def run_pipeline_benchmark(out_root: Path) -> list[PipelineRow]:
    rows: list[PipelineRow] = []
    pipeline_root = out_root / "pipeline_workspaces"
    pipeline_root.mkdir(parents=True, exist_ok=True)

    for spec in PIPELINE_ROBOTS:
        robot_id = spec["robot_id"]
        mjcf_path = Path(spec["mjcf"])
        print(f"\n{'='*72}")
        print(f"PIPELINE: {robot_id}  ({spec['notes']})")
        print(f"  MJCF: {mjcf_path}")
        print(f"{'='*72}")

        row = PipelineRow(
            robot_id=robot_id,
            mjcf=str(mjcf_path),
            expected_class=spec["expected_class"],
            notes=spec["notes"],
        )
        if not mjcf_path.exists():
            row.error = f"MJCF missing: {mjcf_path}"
            rows.append(row)
            continue

        t_start = time.time()
        try:
            cfg = SelfAssembleConfig(
                robot_id=robot_id,
                mjcf_path=mjcf_path,
                workspace_root=pipeline_root,
                mode="local",
                bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                # Trim iteration caps slightly to fail-fast on bad runs
                max_iters_study=14,
                max_iters_generate=20,
                max_iters_validate=22,
                max_iters_export=10,
                max_iters_demo=16,
            )
            with SelfAssemble(cfg) as sa:
                result = sa.run()
            row.workspace = str(result.workspace)
            row.pipeline_ok = result.ok
            for p in result.phases:
                row.phase_results[p.name] = {
                    "ok": p.ok,
                    "duration_sec": p.duration_sec,
                    "tokens": p.token_usage,
                    "error": p.error,
                    "n_artifacts": len(p.artifact_paths),
                }
            # Sum tokens across phases
            tot_in = sum(int(p.token_usage.get("in", 0) or 0) for p in result.phases)
            tot_out = sum(int(p.token_usage.get("out", 0) or 0) for p in result.phases)
            row.total_tokens = {"in": tot_in, "out": tot_out}
        except Exception as e:  # noqa: BLE001
            row.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        row.total_duration_sec = time.time() - t_start
        print(f"  -> pipeline_ok={row.pipeline_ok}  "
              f"dur={row.total_duration_sec:.1f}s  tok={row.total_tokens}")
        for pname, pres in row.phase_results.items():
            flag = "OK " if pres["ok"] else "FAIL"
            print(f"     [{flag}] {pname}  ({pres['duration_sec']:.1f}s)"
                  f"{'  err: ' + pres['error'] if pres['error'] else ''}")
        rows.append(row)

    return rows


# ──────────────────────────────────────────────────────────────────────────
# Part B — Task suite benchmark
# ──────────────────────────────────────────────────────────────────────────


def _link_mjcf(ws: Path, real_mjcf: Path) -> None:
    """Make sure ws/mjcf.xml symlinks to a valid real MJCF."""
    target = ws / "mjcf.xml"
    if not real_mjcf.exists():
        return
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(real_mjcf.resolve())


def run_task_benchmark(
    drivers: list[dict],
    out_root: Path,
) -> list[TaskRow]:
    rows: list[TaskRow] = []
    benchmark_videos = out_root / "task_videos"
    benchmark_videos.mkdir(parents=True, exist_ok=True)

    for d in drivers:
        rid = d["id"]
        ws = Path(d["dir"])
        if not (ws / "driver.py").exists():
            print(f"[skip task suite] {rid}: no driver.py at {ws}")
            continue

        if "mjcf_link" in d:
            _link_mjcf(ws, Path(d["mjcf_link"]))

        # Detect class from driver.py imports for the right task set
        driver_text = (ws / "driver.py").read_text()
        is_quadruped = "QuadrupedPDGaitSkeleton" in driver_text
        tasks = QUADRUPED_TASKS if is_quadruped else ARM_TASKS

        robot_videos = benchmark_videos / rid
        robot_videos.mkdir(exist_ok=True)
        print(f"\n{'='*72}")
        print(f"TASK SUITE: {rid}  ({'quadruped' if is_quadruped else 'arm'})")
        print(f"{'='*72}")

        with TaskPlanner(
            workspace=ws,
            bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            max_iters=25,
            max_tokens_per_turn=5000,
        ) as planner:
            for i, task in enumerate(tasks):
                task_id_int = i + 1
                task_id_str = f"task_{task_id_int:02d}"
                print(f"\n--- {task_id_str} ---  {task[:140]}")
                try:
                    res = planner.execute_task(task, task_id=task_id_str)
                    if res.mp4_path and res.mp4_path.exists():
                        dest = robot_videos / f"{task_id_str}.mp4"
                        dest.write_bytes(res.mp4_path.read_bytes())
                        mp4_str = str(dest)
                    else:
                        mp4_str = None
                    rows.append(TaskRow(
                        robot_id=rid,
                        task_id=task_id_int,
                        task_description=task,
                        ok=res.ok,
                        tool_calls=res.n_tool_calls,
                        frames=res.n_frames,
                        duration_sec=res.duration_sec,
                        tokens=res.token_usage,
                        summary=res.summary,
                        error=res.error,
                        mp4_path=mp4_str,
                    ))
                    print(f"  ok={res.ok} calls={res.n_tool_calls} frames={res.n_frames} "
                          f"dur={res.duration_sec:.1f}s tok={res.token_usage}")
                except Exception as e:  # noqa: BLE001
                    rows.append(TaskRow(
                        robot_id=rid, task_id=task_id_int, task_description=task,
                        ok=False, tool_calls=0, frames=0, duration_sec=0.0,
                        tokens={}, summary="",
                        error=f"{type(e).__name__}: {e}", mp4_path=None,
                    ))
                    print(f"  CRASH: {type(e).__name__}: {e}")
    return rows


# ──────────────────────────────────────────────────────────────────────────
# Scoreboard
# ──────────────────────────────────────────────────────────────────────────


def write_scoreboard(out_path: Path, pipeline_rows: list[PipelineRow],
                     task_rows: list[TaskRow]) -> None:
    lines: list[str] = []
    lines.append("# Cross-robot generalization benchmark — scoreboard\n")
    lines.append(f"_Run: {time.strftime('%Y-%m-%d %H:%M:%S')}, "
                 f"model `us.anthropic.claude-sonnet-4-5-20250929-v1:0`_\n")

    # ── Part A ─────────────────────────────────────────────────────────────
    lines.append("\n## Part A — Pipeline (STUDY → GENERATE → VALIDATE → EXPORT → DEMO)\n")
    lines.append("| Robot | Class | STUDY | GENERATE | VALIDATE | EXPORT | DEMO | Tokens | Wall (s) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in pipeline_rows:
        def ph(name: str) -> str:
            for k in (f"0{i}_{name}" for i in range(1, 6)):
                if k in r.phase_results:
                    return "✅" if r.phase_results[k]["ok"] else "❌"
            return "—"
        tok = r.total_tokens
        tok_str = f"in={tok.get('in', 0)//1000}k+out={tok.get('out', 0)//1000}k" if tok else "—"
        lines.append(f"| {r.robot_id} | {r.expected_class} | "
                     f"{ph('study')} | {ph('generate')} | {ph('validate')} | "
                     f"{ph('export')} | {ph('demo')} | {tok_str} | "
                     f"{r.total_duration_sec:.0f} |")

    n_pipeline = len(pipeline_rows)
    n_ok = sum(1 for r in pipeline_rows if r.pipeline_ok)
    lines.append(f"\n**Pipeline pass rate: {n_ok}/{n_pipeline} = {n_ok/max(n_pipeline,1)*100:.0f}%**\n")

    # ── Part B ─────────────────────────────────────────────────────────────
    lines.append("\n## Part B — Standardized task suite\n")
    by_robot: dict[str, list[TaskRow]] = {}
    for t in task_rows:
        by_robot.setdefault(t.robot_id, []).append(t)

    lines.append("| Robot | Tasks ok | Avg calls | Avg frames | Avg tokens (in/out) | Total $ est |")
    lines.append("|---|---|---|---|---|---|")
    grand_in = grand_out = 0
    for rid, rows in by_robot.items():
        n_ok = sum(1 for r in rows if r.ok)
        avg_calls = sum(r.tool_calls for r in rows) / max(len(rows), 1)
        avg_frames = sum(r.frames for r in rows) / max(len(rows), 1)
        avg_in = sum(r.tokens.get("in", 0) for r in rows) / max(len(rows), 1)
        avg_out = sum(r.tokens.get("out", 0) for r in rows) / max(len(rows), 1)
        tot_in = sum(r.tokens.get("in", 0) for r in rows)
        tot_out = sum(r.tokens.get("out", 0) for r in rows)
        grand_in += tot_in
        grand_out += tot_out
        # Sonnet 4.5: $3/Mtok input, $15/Mtok output
        cost = tot_in * 3e-6 + tot_out * 15e-6
        lines.append(f"| {rid} | {n_ok}/{len(rows)} | {avg_calls:.1f} | {avg_frames:.0f} | "
                     f"{avg_in:.0f}/{avg_out:.0f} | ${cost:.2f} |")

    n_total = len(task_rows)
    n_ok = sum(1 for t in task_rows if t.ok)
    grand_cost = grand_in * 3e-6 + grand_out * 15e-6
    lines.append(f"\n**Task pass rate: {n_ok}/{n_total} = {n_ok/max(n_total,1)*100:.0f}%**")
    lines.append(f"\n**Total task-suite cost: ${grand_cost:.2f}** (Sonnet 4.5)\n")

    # ── Per-task detail ────────────────────────────────────────────────────
    lines.append("\n## Per-task detail\n")
    for rid, rows in by_robot.items():
        lines.append(f"\n### {rid}\n")
        lines.append("| # | Task | OK | Tool calls | Frames | Sim s | Summary |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            summary = (r.summary or "").replace("\n", " ").replace("|", "\\|")
            if len(summary) > 120:
                summary = summary[:117] + "..."
            sim_s = r.frames * 10 * 0.002
            ok_glyph = "✅" if r.ok else "❌"
            lines.append(f"| {r.task_id} | {r.task_description[:60]}{'...' if len(r.task_description)>60 else ''} "
                         f"| {ok_glyph} | {r.tool_calls} | {r.frames} | {sim_s:.1f} | {summary} |")

    out_path.write_text("\n".join(lines))
    print(f"\nScoreboard written: {out_path}")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_benchmark"
    out_root.mkdir(exist_ok=True)

    print("\n" + "=" * 72)
    print("CROSS-ROBOT GENERALIZATION BENCHMARK")
    print("=" * 72)

    # Part A
    print("\n>>> Part A: pipeline benchmark on 4 new robots")
    pipeline_rows = run_pipeline_benchmark(out_root)

    # Add the newly-built drivers to the task-suite list
    drivers = list(EXISTING_DRIVER_DIRS)
    for r in pipeline_rows:
        if r.workspace and r.pipeline_ok:
            ws = Path(r.workspace)
            drivers.append({"id": r.robot_id, "dir": ws,
                            "mjcf_link": Path(r.mjcf)})
        elif r.workspace:
            # Even if pipeline failed, the driver may have been written
            ws = Path(r.workspace)
            if (ws / "driver.py").exists():
                drivers.append({"id": r.robot_id, "dir": ws,
                                "mjcf_link": Path(r.mjcf)})

    # Part B
    print(f"\n>>> Part B: task suite on {len(drivers)} robots")
    task_rows = run_task_benchmark(drivers, out_root)

    # Persist
    (out_root / "benchmark_summary.json").write_text(json.dumps({
        "pipeline": [vars(r) for r in pipeline_rows],
        "tasks": [vars(r) for r in task_rows],
    }, indent=2, default=str))
    write_scoreboard(out_root / "SCOREBOARD.md", pipeline_rows, task_rows)

    print(f"\n{'='*72}\nBENCHMARK COMPLETE — see {out_root}/SCOREBOARD.md\n{'='*72}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
