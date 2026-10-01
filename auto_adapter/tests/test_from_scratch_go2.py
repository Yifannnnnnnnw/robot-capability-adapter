# SPDX-License-Identifier: Apache-2.0
"""From-scratch driver synthesis on Go2 (4-leg quadruped, 12 DOF, torque motors).

This is the most informative test of "agent picks algorithm based on robot":
  - Go2 has NO end-effector to reach with IK
  - Behaviors are locomotion (stand / sit / walk), not manipulation
  - Actuators are TORQUE (not position like arms) — needs PD control
  - Walking needs a gait pattern (sin-wave or convex MPC or RL policy)

If the from-scratch synthesis is real, the agent should NOT write DLS IK
for Go2. It should write joint-space PD + a gait, totally different
algorithm class.
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
    REPO_ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml"
)


def main() -> None:
    assert MJCF_PATH.exists(), f"missing MJCF at {MJCF_PATH}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_fromscratch_go2_"))
    cfg = FromScratchConfig(
        robot_id="go2_from_scratch",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters_study=20,
        max_iters_gen_algo=50,
    )
    print(f">> workspace: {workspace_root / cfg.robot_id}")

    with FromScratchOrchestrator(cfg) as orch:
        result = orch.run()

    print(f"\n{'='*72}")
    print(f"GO2 FROM-SCRATCH (quadruped, 12-DOF torque)")
    print(f"{'='*72}")
    print(f"  study_ok:     {result.study_ok}")
    print(f"  gen_ok:       {result.gen_ok}")
    print(f"  validate_ok:  {result.validate_ok}")
    print(f"  duration:     {result.total_duration_sec:.1f}s")
    cost = (result.total_tokens.get("in", 0) * 3e-6
            + result.total_tokens.get("out", 0) * 15e-6)
    print(f"  tokens:       in={result.total_tokens.get('in')} "
          f"out={result.total_tokens.get('out')}")
    print(f"  cost:         ${cost:.2f}")
    if result.error:
        print(f"  error:        {result.error}")

    print(f"\n=== validate_report ===")
    print(json.dumps(result.validate_report, indent=2))

    if result.driver_path and result.driver_path.exists():
        src = result.driver_path.read_text()
        n_lines = len(src.splitlines())
        print(f"\n=== source-code analysis ({n_lines} lines) ===")
        # Heuristic: did agent pick algorithm class APPROPRIATE for quadruped?
        # Should mention: pd / torque / gait / trot / locomotion / stance / swing / kp / kd
        # Should NOT center around: jacobian / dls / inverse_kinematics / ee_site / move_cartesian
        markers = {
            "PD control": any(m in src for m in ["kp", "k_p", "Kp"]),
            "torque": "torque" in src.lower() or "tau" in src.lower(),
            "gait/stance/swing": any(m in src.lower() for m in
                                     ["gait", "stance", "swing", "trot", "stride", "cycle"]),
            "PD constants kp+kd": "kd" in src.lower() or "k_d" in src.lower(),
            "stand_up method": "def stand_up" in src,
            "sit method": "def sit" in src,
            "walk method": "def walk" in src,
            "Jacobian-based IK (UNEXPECTED for quad)": "compute_jacobian" in src or "ik_dls" in src,
            "inverse_kinematics fn (UNEXPECTED)": "def inverse_kinematics" in src,
        }
        for marker, found in markers.items():
            tag = "✓" if found else "✗"
            print(f"  {tag} {marker}")

        # Also: did it correctly use ee_body=None / no IK?
        print(f"\n=== first 60 lines of driver ===")
        for i, line in enumerate(src.splitlines()[:60], 1):
            print(f"  {i:3d}: {line}")

    # Save artifacts
    final_dir = REPO_ROOT / "artifacts/auto_adapter_from_scratch_go2_artifacts"
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
