# SPDX-License-Identifier: Apache-2.0
"""Baseline ablation: framework + skeleton vs. raw `import mujoco`.

Addresses the obvious reviewer question:
  "Why is your framework better than just letting Claude write a Python
   script that uses mujoco directly?"

Setup:
  - SAME tasks, SAME robot (SO-101 + Franka).
  - Config A (framework): the existing TaskPlanner — tool surface is the
    skeleton's public methods (move_cartesian, gripper_close, etc.).
  - Config B (raw): a stripped TaskPlanner whose ONLY tool is
    execute_python, running in a Python subprocess that has `mujoco`
    installed. Agent has to import mujoco, build a model, write its own
    IK / motion code per task.

Metrics:
  - task pass / fail
  - tool calls (or LLM iters)
  - tokens in / out → $ cost
  - wall-clock per task
  - lines of Python the agent ended up writing (for B; for A it's just
    the parameters it filled into the skeleton)

The hypothesis: framework wins on cost + reliability; raw might win on
task generality (an open-ended task framework doesn't expose). Either
result is interesting for the paper.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.react_loop import ReactLoop, ToolSpec
from auto_adapter.agent.task_planner import TaskPlanner


# ──────────────────────────────────────────────────────────────────────────
# Shared task set (subset of simple-task suite — focused on what raw-mujoco
# might plausibly handle)
# ──────────────────────────────────────────────────────────────────────────


_BASELINE_TASKS = [
    (
        "move_x_plus_5cm",
        "Move the end-effector +5 cm in X from its current position, hold "
        "briefly, then return to home. The MJCF is at 'mjcf.xml' (workspace-"
        "relative). The arm is a 5-DOF SO-101.",
    ),
    (
        "trace_square_xy",
        "Trace a small 4 cm square in the XY plane starting from the current "
        "end-effector position, returning to start. The MJCF is at "
        "'mjcf.xml'. The arm is a 5-DOF SO-101.",
    ),
    (
        "open_close_gripper",
        "Open then close the gripper of the SO-101 arm. Report whether the "
        "gripper actually moved (e.g. by reading the jaw joint position "
        "before and after). MJCF at 'mjcf.xml'.",
    ),
]


# ──────────────────────────────────────────────────────────────────────────
# Config B: raw-mujoco runner (one execute_python tool, no skeleton)
# ──────────────────────────────────────────────────────────────────────────


_RAW_SYSTEM = """\
You are operating a robot in simulation. You have ONE tool: execute_python.
You can run arbitrary Python code in a sandboxed subprocess that has the
mujoco package installed.

You must:
  1. Load the MJCF file via mujoco.MjModel.from_xml_path(...).
  2. Construct an MjData.
  3. Implement whatever motion / IK / gripper control logic the task requires.
  4. Run mj_step() in a loop to advance physics.
  5. Read sensor / qpos data to verify outcomes.

You have NO precomputed skeleton class. No DLS IK helper. No move_cartesian.
Write the math yourself if you need it. State persists across execute_python
calls in the same task: variables defined in call 1 are available in call 2.

When done, give a one-sentence summary of what happened + the final EE
position or whatever metric the task asked for.
"""


def _make_raw_exec_tool(workspace: Path, log: list) -> ToolSpec:
    """execute_python that runs in a SUBPROCESS preserving state across
    calls in the same task. State persistence is implemented by serializing
    the locals dict between calls (pickle to a tmpfile per-task)."""
    state_dir = workspace / "_raw_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_pkl = state_dir / "state.pkl"
    # Clear any stale state from prior task at construction
    if state_pkl.exists():
        state_pkl.unlink()

    def _handler(inp: dict) -> dict:
        code = inp["code"]
        # Wrap user code so that locals are loaded from state.pkl at start
        # and saved to state.pkl at end. Anything pickle-friendly persists.
        wrapper = f"""
import pickle, sys
from pathlib import Path
state_pkl = Path({str(state_pkl)!r})
g = {{}}
if state_pkl.exists():
    try:
        with state_pkl.open('rb') as f:
            g = pickle.load(f)
    except Exception as _e:
        sys.stderr.write(f'state load fail: {{_e}}\\n')
        g = {{}}
g['__name__'] = '__main__'
try:
    exec(compile({code!r}, '<task_code>', 'exec'), g)
finally:
    # Save back what's pickleable
    out = {{}}
    for k, v in g.items():
        if k.startswith('_') or k == '__builtins__':
            continue
        try:
            pickle.dumps(v)
            out[k] = v
        except Exception:
            pass
    with state_pkl.open('wb') as f:
        pickle.dump(out, f)
"""
        t0 = time.time()
        try:
            cp = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True, text=True, timeout=180,
                cwd=str(workspace),
                env={**os.environ, "PYTHONPATH":
                     f"{workspace}:{os.environ.get('PYTHONPATH', '')}"},
            )
            out = {
                "exit_code": cp.returncode,
                "stdout": cp.stdout[:8000],
                "stderr": cp.stderr[:4000],
            }
        except subprocess.TimeoutExpired as e:
            out = {
                "exit_code": -1,
                "stdout": (e.stdout or "")[:8000] if isinstance(e.stdout, str) else "",
                "stderr": (e.stderr or "")[:4000] if isinstance(e.stderr, str) else "",
                "error": "timeout 180s",
            }
        log.append({
            "tool": "execute_python",
            "code_chars": len(code),
            "exit_code": out["exit_code"],
            "dur_ms": (time.time() - t0) * 1000.0,
        })
        return out

    return ToolSpec(
        name="execute_python",
        description=(
            "Execute Python code in a subprocess with `mujoco` and `numpy` "
            "available. State (variables, imports) persists across calls in "
            "the same task. Returns {exit_code, stdout, stderr}. Useful for "
            "loading MJCFs, building MjData, running mj_step in loops, and "
            "computing IK / control logic from scratch."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        handler=_handler,
    )


def _run_raw(workspace: Path, task_id: str, task_desc: str,
             model: str, region: str) -> dict:
    """Run one task in 'raw mujoco' mode — agent only gets execute_python."""
    call_log: list = []
    tool = _make_raw_exec_tool(workspace, call_log)
    trace = workspace / "task_traces" / f"raw_{task_id}.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    loop = ReactLoop(
        tools=[tool],
        system=_RAW_SYSTEM,
        model=model, region=region,
        max_iters=30, max_tokens_per_turn=6000,
        trace_path=trace,
    )
    t0 = time.time()
    res = loop.run(task_desc)
    dur = time.time() - t0
    return {
        "mode": "raw",
        "task_id": task_id,
        "ok": res.ok,
        "n_tool_calls": len(call_log),
        "duration_sec": dur,
        "tokens": res.total_tokens,
        "summary": res.final_text,
        "error": res.error,
    }


# ──────────────────────────────────────────────────────────────────────────
# Config A: framework runner (same as TaskPlanner used elsewhere)
# ──────────────────────────────────────────────────────────────────────────


def _run_framework(workspace: Path, task_id: str, task_desc: str,
                   model: str, region: str) -> dict:
    with TaskPlanner(
        workspace=workspace,
        bedrock_model=model, region=region,
        max_iters=20, max_tokens_per_turn=5000,
    ) as p:
        r = p.execute_task(task_desc, task_id=f"baseline_fw_{task_id}",
                            capture_video=False)
    return {
        "mode": "framework",
        "task_id": task_id,
        "ok": r.ok,
        "n_tool_calls": r.n_tool_calls,
        "duration_sec": r.duration_sec,
        "tokens": r.token_usage,
        "summary": r.summary,
        "error": r.error,
    }


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_baseline_ablation"
    out_root.mkdir(exist_ok=True)
    ws = REPO_ROOT / "artifacts/auto_adapter_so101_v2_artifacts"

    model = os.environ.get(
        "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")

    print("=" * 72)
    print("BASELINE ABLATION: framework vs raw mujoco")
    print("=" * 72)

    results: list[dict] = []
    for task_id, task_desc in _BASELINE_TASKS:
        print(f"\n--- {task_id} ---")
        print(f"  task: {task_desc[:160]}")
        # Run framework first (cheaper, faster)
        print("\n  [Config A: framework]")
        r_fw = _run_framework(ws, task_id, task_desc, model, region)
        print(f"    ok={r_fw['ok']} calls={r_fw['n_tool_calls']} "
              f"dur={r_fw['duration_sec']:.1f}s tok={r_fw['tokens']}")
        # Run raw mode
        print("\n  [Config B: raw mujoco]")
        r_raw = _run_raw(ws, task_id, task_desc, model, region)
        print(f"    ok={r_raw['ok']} calls={r_raw['n_tool_calls']} "
              f"dur={r_raw['duration_sec']:.1f}s tok={r_raw['tokens']}")
        results.append({"task_id": task_id, "task": task_desc,
                        "framework": r_fw, "raw": r_raw})

    (out_root / "baseline_summary.json").write_text(
        json.dumps(results, indent=2, default=str))

    print("\n" + "=" * 72)
    print("SCOREBOARD")
    print("=" * 72)
    print(f"{'Task':<25}{'Framework':>40} | {'Raw mujoco':>40}")
    print(f"{'':<25}{'ok / calls / $':>40} | {'ok / calls / $':>40}")
    print("-" * 110)
    fw_tot_in = fw_tot_out = raw_tot_in = raw_tot_out = 0
    for r in results:
        fw = r["framework"]
        raw = r["raw"]
        fw_in = fw["tokens"].get("in", 0)
        fw_out = fw["tokens"].get("out", 0)
        raw_in = raw["tokens"].get("in", 0)
        raw_out = raw["tokens"].get("out", 0)
        fw_cost = fw_in * 3e-6 + fw_out * 15e-6
        raw_cost = raw_in * 3e-6 + raw_out * 15e-6
        fw_tot_in += fw_in; fw_tot_out += fw_out
        raw_tot_in += raw_in; raw_tot_out += raw_out
        fw_str = f"{'OK' if fw['ok'] else 'FAIL':>3} / {fw['n_tool_calls']:>3} / ${fw_cost:.2f}"
        raw_str = f"{'OK' if raw['ok'] else 'FAIL':>3} / {raw['n_tool_calls']:>3} / ${raw_cost:.2f}"
        print(f"{r['task_id']:<25}{fw_str:>40} | {raw_str:>40}")
    print("-" * 110)
    fw_total = fw_tot_in * 3e-6 + fw_tot_out * 15e-6
    raw_total = raw_tot_in * 3e-6 + raw_tot_out * 15e-6
    print(f"{'TOTAL':<25}{'$' + str(round(fw_total, 2)):>40} | {'$' + str(round(raw_total, 2)):>40}")
    print()
    fw_pass = sum(1 for r in results if r["framework"]["ok"])
    raw_pass = sum(1 for r in results if r["raw"]["ok"])
    print(f"  Pass rate:   framework {fw_pass}/{len(results)}    "
          f"raw {raw_pass}/{len(results)}")
    print(f"  Cost ratio:  raw is {raw_total / max(fw_total, 0.01):.1f}× framework")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
