# SPDX-License-Identifier: Apache-2.0
"""From-scratch driver synthesis on Franka (7-DOF redundant arm).

The interesting question: does the agent pick a DIFFERENT IK method when
the arm is redundant (7 DOF, 3 task-space DOF for position-only → 4-dim
nullspace)? For SO-101 (5-DOF, no redundancy) it picked plain DLS — for
Franka a nullspace-projected DLS or pseudoinverse + secondary task is the
textbook choice.

Also: Franka has NO <site> in its MJCF — agent must use ee_body_name.
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

MJCF_PATH = (
    REPO_ROOT / "assets" / "mjcf" / "franka_panda" / "scene.xml"
)


def main() -> None:
    assert MJCF_PATH.exists(), f"missing MJCF at {MJCF_PATH}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_fromscratch_franka_"))
    cfg = FromScratchConfig(
        robot_id="franka_from_scratch",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters_study=20,     # Franka MJCF richer than SO-101 (no <site>, tendons)
        max_iters_gen_algo=45,   # extra room for redundancy handling
    )
    print(f">> workspace: {workspace_root / cfg.robot_id}")

    with FromScratchOrchestrator(cfg) as orch:
        result = orch.run()

    print(f"\n{'='*72}")
    print(f"FRANKA FROM-SCRATCH")
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

    print(f"\n=== validate_report ===")
    print(json.dumps(result.validate_report, indent=2))

    # Comparison summary vs SO-101
    so101_dir = REPO_ROOT / "artifacts/auto_adapter_from_scratch_so101_artifacts"
    if (so101_dir / "validate_report.json").exists():
        so101_report = json.loads((so101_dir / "validate_report.json").read_text())
        so101_metrics = so101_report.get("source_metrics", {})
        fr_metrics = result.validate_report.get("source_metrics", {})
        print(f"\n=== COMPARISON: SO-101 (5-DOF) vs Franka (7-DOF redundant) ===")
        def _fmt(v, w=6):
            if v is None: return f"{'(n/a)':>{w}}"
            try: return f"{v:>{w}}"
            except (TypeError, ValueError): return f"{str(v):>{w}}"
        print(f"  Lines of code:")
        print(f"    SO-101:  {_fmt(so101_metrics.get('n_lines'))}")
        print(f"    Franka:  {_fmt(fr_metrics.get('n_lines'))}")
        print(f"  IK method picked:")
        print(f"    SO-101:  {so101_metrics.get('ik_method_hint')!r}")
        print(f"    Franka:  {fr_metrics.get('ik_method_hint')!r}")
        # IK roundtrip error
        def _ik_err(rep):
            for t in rep.get("tests", []):
                if t["test"] == "ik_roundtrip":
                    return t.get("metric")
            return None
        print(f"  IK roundtrip error (m):")
        print(f"    SO-101:  {_ik_err(so101_report)}")
        print(f"    Franka:  {_ik_err(result.validate_report)}")

    if result.driver_path and result.driver_path.exists():
        # Show the IK function the agent wrote
        src = result.driver_path.read_text()
        print(f"\n=== driver_from_scratch.py: IK function ===")
        lines = src.splitlines()
        ik_start = None
        for i, line in enumerate(lines):
            if "def inverse_kinematics" in line or "def ik(" in line or "def _ik(" in line:
                ik_start = i
                break
        if ik_start is not None:
            for i, line in enumerate(lines[ik_start:ik_start + 60], ik_start + 1):
                print(f"  {i:3d}: {line}")

    # Save artifacts
    final_dir = REPO_ROOT / "artifacts/auto_adapter_from_scratch_franka_artifacts"
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
