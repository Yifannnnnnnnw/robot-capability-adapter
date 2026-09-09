# SPDX-License-Identifier: Apache-2.0
"""End-to-end SelfAssemble on the Piper 6-DOF arm.

Compared to SO-101 this tests:
  - Different DOF count (6 instead of 5)
  - Parallel-jaw slide gripper (instead of hinge jaw)
  - NO weld constraints in the MJCF → exercises ContactGraspBackend
  - Different MJCF style (mujoco_menagerie layout)

Same orchestrator + same prompts + same skeletons must produce a working
driver and recorded validation/demo videos without any per-robot tweaks.
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

PIPER_MJCF = (
    REPO_ROOT / "assets" / "mjcf" / "piper" / "scene.xml"
)


def main() -> None:
    assert PIPER_MJCF.exists(), f"Piper scene MJCF missing at {PIPER_MJCF}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_full_piper_"))
    cfg = SelfAssembleConfig(
        robot_id="piper_full",
        mjcf_path=PIPER_MJCF,
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

    # ─── STUDY+GENERATE must work; report VALIDATE/DEMO as informational ─
    for phase_name in ("01_study", "02_generate"):
        p = next((p for p in result.phases if p.name == phase_name), None)
        assert p is not None and p.ok, (
            f"{phase_name} failed: {(p.error if p else 'missing')}"
        )

    # ─── driver.py: should pick ArmSerialDLSSkeleton ─────────────────────
    sys.path.insert(0, str(ws))
    sys.modules.pop("driver", None)
    sys.modules.pop("mcp_server", None)
    orig_cwd = os.getcwd()
    try:
        os.chdir(ws)
        driver = importlib.import_module("driver")
        skel = driver.build()
        cls_name = type(skel).__name__
        spec = skel.spec
        print(f"\n  ✓ driver built a {cls_name}")
        print(f"  ✓ DOF: {len(spec.arm_joint_names)} arm joints + "
              f"{len(spec.gripper_actuator_names or [])} gripper actuators")
        print(f"  ✓ ee_site: {spec.ee_site_name}")
        print(f"  ✓ grasp_backend: {spec.grasp_backend} → "
              f"{type(skel.grasp_backend).__name__}")
        # Piper has no welds: backend should be "contact" or auto-picked to it
        actual_backend = type(skel.grasp_backend).__name__
        assert "Contact" in actual_backend or "NoOp" in actual_backend, (
            f"Piper has no weld constraints — expected Contact/NoOp backend, "
            f"got {actual_backend}"
        )
    finally:
        os.chdir(orig_cwd)

    # ─── validate report ─────────────────────────────────────────────────
    vrep = ws / "validate_report.json"
    if vrep.exists():
        report = json.loads(vrep.read_text())
        for t in report.get("tests", []):
            print(f"  validate: {t['test']} → ok={t['ok']} "
                  f"metric={t.get('metric')} detail={t.get('detail','')[:80]}")

    # ─── demo + recordings ───────────────────────────────────────────────
    demo_mp4 = ws / "demo.mp4"
    if demo_mp4.exists():
        print(f"  ✓ demo.mp4 = {demo_mp4.stat().st_size/1024:.1f} KB")
    rec = sorted((ws / "recordings").glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    print(f"  recordings: {len(rec)} mp4(s)")

    print(f"\n[PIPELINE COMPLETE]  workspace: {ws}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
