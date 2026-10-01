# SPDX-License-Identifier: Apache-2.0
"""Vision-only task suite — get_object_position is HIDDEN.

This tests whether the agent can plan + execute grasping when its only
source of object positions is the look_at_scene() vision tool. The VLM
gives coarse estimates (~5-20 cm error in our SO-101 calibration); to
actually grasp, the agent must combine vision with EE-relative refinement.

Two task variants:

  (A) FIND-AND-NAME tasks — semantic identification, no grasping
      "Look at the scene. Tell me what color the smallest object is."

  (B) FIND-AND-GRASP tasks — semantic identification + manipulation
      "Look at the scene. Pick up the RED object and lift it 10 cm."

Compare to baseline where get_object_position IS available.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import TaskPlanner
from auto_adapter.agent.vision_tool import make_look_at_scene_tool


# ──────────────────────────────────────────────────────────────────────────
# Vision-dependent tasks
# ──────────────────────────────────────────────────────────────────────────


_VISION_TASKS = [
    (
        "identify_red",
        "You MUST use look_at_scene first (don't guess from memory). "
        "Report what the vision tool returned, and identify which object is RED.",
    ),
    (
        "identify_count",
        "Call look_at_scene first, then report how many distinct graspable "
        "objects the vision tool returned and list their colors. Do not grasp.",
    ),
    (
        "grasp_red",
        "STEP 1: Call look_at_scene to identify the RED object and get its "
        "VLM-estimated position. NOTE: VLM position estimates have ~10-20 cm "
        "error — you MUST refine. "
        "STEP 2: Move to the VLM-estimated position +5 cm above. "
        "STEP 3: Call look_at_scene AGAIN from this new viewpoint to get a "
        "refined estimate (closer = better). "
        "STEP 4: Iteratively descend + close gripper. "
        "Up to 3 grasp attempts. Report success/fail + which object you "
        "grasped (if any).",
    ),
    (
        "grasp_leftmost",
        "Call look_at_scene to get a list of object positions. Identify the "
        "one with smallest X (most-negative or leftmost in world frame). "
        "Then EXACTLY 1 more look_at_scene call after moving over it to "
        "refine the position. Then attempt grasp + lift 10 cm. "
        "Report which object you targeted.",
    ),
]


# Patched TaskPlanner that REMOVES get_object_position and adds look_at_scene
class VisionOnlyTaskPlanner(TaskPlanner):
    def _build_tools(self, skel, capture, call_log):
        tools = super()._build_tools(skel, capture, call_log)
        # Drop get_object_position so the agent CAN'T cheat
        tools = [t for t in tools if t.name != "get_object_position"]
        # Add look_at_scene
        scene_hint = (
            "SO-101 tabletop with 6 graspable objects: banana (yellow), "
            "mug (light/white), bottle (blue cylinder), screwdriver "
            "(dark handle metallic shaft), duck (yellow toy), lego (red brick)."
        )
        vision = make_look_at_scene_tool(
            skel,
            bedrock_model=self.bedrock_model,
            region=self.region,
            save_dir=self.workspace / "vision_views",
            scene_hint=scene_hint,
            call_log=call_log,
        )
        tools.append(vision)
        return tools


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_vision_tasks"
    out_root.mkdir(exist_ok=True)
    ws = REPO_ROOT / "artifacts/auto_adapter_so101_v2_artifacts"

    results: list[dict] = []

    print("=" * 72)
    print("VISION-ONLY TASK SUITE (no get_object_position)")
    print("=" * 72)
    with VisionOnlyTaskPlanner(
        workspace=ws,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
        max_iters=30,
        max_tokens_per_turn=5000,
    ) as planner:
        for task_id, task_desc in _VISION_TASKS:
            print(f"\n--- {task_id} ---")
            print(f"  task: {task_desc[:160]}")
            r = planner.execute_task(task_desc, task_id=task_id)
            print(f"  ok={r.ok} calls={r.n_tool_calls} frames={r.n_frames} "
                  f"dur={r.duration_sec:.1f}s tok={r.token_usage}")
            print(f"  summary: {(r.summary or '')[:240]}")
            # Save mp4
            if r.mp4_path and r.mp4_path.exists():
                dest = out_root / f"{task_id}.mp4"
                dest.write_bytes(r.mp4_path.read_bytes())
            # Tally vision tool usage
            vision_calls = [c for c in r.tool_call_log if c["tool"] == "look_at_scene"]
            print(f"  vision_calls: {len(vision_calls)}")
            results.append({
                "task_id": task_id, "task": task_desc, "ok": r.ok,
                "tool_calls": r.n_tool_calls,
                "vision_calls": len(vision_calls),
                "frames": r.n_frames, "duration_sec": r.duration_sec,
                "tokens": r.token_usage, "summary": r.summary,
                "error": r.error,
            })

    (out_root / "vision_summary.json").write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'='*72}\nSCOREBOARD\n{'='*72}")
    n_ok = sum(1 for r in results if r["ok"])
    print(f"  {n_ok}/{len(results)} tasks passed (vision-only mode, no get_object_position)")
    tot_in = sum(r["tokens"].get("in", 0) for r in results)
    tot_out = sum(r["tokens"].get("out", 0) for r in results)
    print(f"  tokens: {tot_in} in / {tot_out} out = ${tot_in*3e-6 + tot_out*15e-6:.2f}")
    for r in results:
        flag = "OK " if r["ok"] else "FAIL"
        print(f"  [{flag}] {r['task_id']:20s} vision_calls={r['vision_calls']} "
              f"tool_calls={r['tool_calls']}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
