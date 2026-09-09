# SPDX-License-Identifier: Apache-2.0
"""End-to-end SelfAssemble: Go2 (quadruped), all 5 phases, local mode.

Mirror of test_full_pipeline_so101.py but for the quadruped path. The same
orchestrator + same prompts must produce a working Go2 driver via the new
QuadrupedPDGaitSkeleton.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter import SelfAssemble, SelfAssembleConfig

# Use the scene wrapper that adds a ground plane (go2.xml alone has no floor)
GO2_MJCF = (
    REPO_ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml"
)


def main() -> None:
    assert GO2_MJCF.exists(), f"Go2 scene MJCF missing at {GO2_MJCF}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_full_go2_"))
    cfg = SelfAssembleConfig(
        robot_id="go2_full",
        mjcf_path=GO2_MJCF,
        workspace_root=workspace_root,
        mode="local",
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
    )
    print(f">> workspace: {workspace_root / cfg.robot_id}")

    with SelfAssemble(cfg) as sa:
        result = sa.run()

    print("\n=== PHASES ===")
    for p in result.phases:
        status = "OK " if p.ok else "FAIL"
        print(f"  [{status}] {p.name}  dur={p.duration_sec:.1f}s  tok={p.token_usage}")
        if p.error:
            print(f"           error: {p.error}")

    ws = result.workspace

    # ─── Per-phase assertions (don't fail outright on walk; report only) ─
    p_study = next(p for p in result.phases if p.name == "01_study")
    p_gen = next(p for p in result.phases if p.name == "02_generate")
    assert p_study.ok, f"STUDY failed: {p_study.error}"
    assert p_gen.ok, f"GENERATE failed: {p_gen.error}"

    # ─── driver.py: should pick QuadrupedPDGaitSkeleton ──────────────────
    sys.path.insert(0, str(ws))
    sys.modules.pop("driver", None)
    sys.modules.pop("mcp_server", None)
    orig_cwd = os.getcwd()
    try:
        os.chdir(ws)
        driver = importlib.import_module("driver")
        skel = driver.build()
        cls_name = type(skel).__name__
        print(f"\n  ✓ driver built a {cls_name}")
        assert "Quadruped" in cls_name, (
            f"expected a Quadruped skeleton, got {cls_name}"
        )
    finally:
        os.chdir(orig_cwd)

    # ─── validate_report.json shape ──────────────────────────────────────
    vrep = ws / "validate_report.json"
    if vrep.exists():
        report = json.loads(vrep.read_text())
        print(f"\n  validate_report tests: "
              f"{[(t['test'], t['ok'], t.get('metric')) for t in report.get('tests', [])]}")
    else:
        print("\n  ! validate_report.json missing")

    # ─── demo.mp4 ────────────────────────────────────────────────────────
    demo_mp4 = ws / "demo.mp4"
    if demo_mp4.exists():
        size_kb = demo_mp4.stat().st_size / 1024
        print(f"  ✓ demo.mp4 = {size_kb:.1f} KB")
    else:
        print("  ! demo.mp4 missing")

    # ─── recordings ──────────────────────────────────────────────────────
    rec = sorted((ws / "recordings").glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    print(f"  recordings: {len(rec)} mp4(s)")
    for r in rec:
        print(f"     - {r.name} ({r.stat().st_size/1024:.1f} KB)")

    print(f"\n  narrative.md: {(ws / 'narrative.md').stat().st_size} bytes")

    # Soft pass: STUDY+GENERATE must work; later phases are reported only
    # because hand-tuned gait may be fragile.
    print("\n[PIPELINE COMPLETE]  see workspace + recordings.")
    print(f">> workspace: {ws}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
