# SPDX-License-Identifier: Apache-2.0
"""From-scratch driver synthesis on SO-101.

This test answers the deepest reviewer criticism: "Is the agent just
filling specs, or can it actually synthesize control algorithms?"

The agent is forbidden from importing auto_adapter.skeletons. It must
read the MJCF, derive the kinematic chain, and write its own FK + IK +
motion + grasp code. Then we exercise the resulting driver and report:

  - Did it produce a working Robot class?
  - Which IK method did it pick? (heuristic from source)
  - How accurate is its IK round-trip?
  - How many lines of code did it write?

Expected cost: ~$5-10 (agent runs lots of iters to prototype + write code).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)

MJCF_PATH = REPO_ROOT / "assets" / "mjcf" / "so101_mujoco.xml"


def main() -> None:
    assert MJCF_PATH.exists(), f"missing MJCF at {MJCF_PATH}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_fromscratch_so101_"))
    cfg = FromScratchConfig(
        robot_id="so101_from_scratch",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters_study=14,
        max_iters_gen_algo=40,
    )
    print(f">> workspace: {workspace_root / cfg.robot_id}")

    with FromScratchOrchestrator(cfg) as orch:
        result = orch.run()

    print(f"\n{'='*72}")
    print(f"FROM-SCRATCH RESULT")
    print(f"{'='*72}")
    print(f"  study_ok:     {result.study_ok}")
    print(f"  gen_ok:       {result.gen_ok}")
    print(f"  validate_ok:  {result.validate_ok}")
    print(f"  duration:     {result.total_duration_sec:.1f}s")
    print(f"  tokens:       in={result.total_tokens.get('in')} "
          f"out={result.total_tokens.get('out')}")
    cost = (result.total_tokens.get("in", 0) * 3e-6
            + result.total_tokens.get("out", 0) * 15e-6)
    print(f"  cost:         ${cost:.2f}")
    if result.error:
        print(f"  error:        {result.error}")
    print(f"  driver:       {result.driver_path}")

    print(f"\n=== validate_report.json ===")
    print(json.dumps(result.validate_report, indent=2))

    if result.driver_path and result.driver_path.exists():
        print(f"\n=== driver_from_scratch.py (first 80 lines) ===")
        for i, line in enumerate(result.driver_path.read_text().splitlines()[:80], 1):
            print(f"  {i:3d}: {line}")
        n_lines = len(result.driver_path.read_text().splitlines())
        if n_lines > 80:
            print(f"  ... ({n_lines - 80} more lines)")

    # Save artifacts to a stable path
    final_dir = REPO_ROOT / "artifacts/auto_adapter_from_scratch_so101_artifacts"
    if (workspace_root / cfg.robot_id).exists():
        import shutil
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(workspace_root / cfg.robot_id, final_dir)
        print(f"\n  artifacts saved to: {final_dir}")

    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
