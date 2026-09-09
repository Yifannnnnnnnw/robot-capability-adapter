# SPDX-License-Identifier: Apache-2.0
"""From-scratch driver synthesis on Piper + pickbench MJCF.

Re-synthesizes a Piper driver against the pickbench scene (3 graspable
cubes + weld equalities) so the agent ALSO writes the grasp code, not
just arm kinematics. Outputs to artifacts/auto_adapter_from_scratch_piper_pickbench_artifacts/.

Cited in §4.6.4 to remove the "scene-side grasp" disclosure.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)

MJCF_PATH = REPO_ROOT / "assets" / "mjcf" / "piper" / "pickbench.xml"


def main() -> None:
    assert MJCF_PATH.exists(), f"missing MJCF at {MJCF_PATH}"

    workspace_root = REPO_ROOT / "artifacts"
    workspace_root.mkdir(parents=True, exist_ok=True)
    cfg = FromScratchConfig(
        robot_id="from_scratch_piper_pickbench",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters_study=20,
        max_iters_gen_algo=45,
    )
    print(f">> workspace: {workspace_root / ('auto_adapter_' + cfg.robot_id + '_artifacts')}")

    with FromScratchOrchestrator(cfg) as orch:
        result = orch.run()

    print(f"\n{'='*72}")
    print(f"PIPER + PICKBENCH FROM-SCRATCH")
    print(f"{'='*72}")
    print(f"  study_ok:     {result.study_ok}")
    print(f"  gen_ok:       {result.gen_ok}")
    print(f"  validate_ok:  {result.validate_ok}")
    print(f"  duration:     {result.total_duration_sec:.1f}s")
    print(f"  tokens:       in={result.total_tokens.get('in')} "
          f"out={result.total_tokens.get('out')}")
    cost = (result.total_tokens.get("in", 0) * 3e-6
            + result.total_tokens.get("out", 0) * 15e-6)
    print(f"  cost (Sonnet 4.5): ${cost:.2f}")
    out = workspace_root / f"auto_adapter_{cfg.robot_id}_artifacts"
    print(f"  artifact dir:    {out}")


if __name__ == "__main__":
    main()
