# SPDX-License-Identifier: Apache-2.0
"""End-to-end SelfAssemble on the Franka Emika Panda 7-DOF arm.

Compared to SO-101 and Piper, Franka tests:
  - 7-DOF arm (instead of 5 or 6) — IK with more DOFs
  - NO <site> in the MJCF — exercises the new ee_body_name fallback
  - Tendon-coupled parallel gripper (actuator8 drives "split" tendon)
  - Different per-joint actuator gains (general/affine, not position)
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

FRANKA_MJCF = (
    REPO_ROOT / "assets" / "mjcf" / "franka_panda" / "scene.xml"
)


def main() -> None:
    assert FRANKA_MJCF.exists(), f"Franka scene MJCF missing at {FRANKA_MJCF}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_full_franka_"))
    cfg = SelfAssembleConfig(
        robot_id="franka_panda",
        mjcf_path=FRANKA_MJCF,
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

    for phase_name in ("01_study", "02_generate"):
        p = next((p for p in result.phases if p.name == phase_name), None)
        assert p is not None and p.ok, (
            f"{phase_name} failed: {(p.error if p else 'missing')}"
        )

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
        print(f"  ✓ DOF: {len(spec.arm_joint_names)} arm joints")
        print(f"  ✓ EE: site={spec.ee_site_name!r}  body={spec.ee_body_name!r}")
        # Franka has NO site, agent MUST have used ee_body_name
        assert spec.ee_body_name is not None and spec.ee_site_name is None, (
            f"Franka has no <site> — expected ee_body_name set, got "
            f"site={spec.ee_site_name!r} body={spec.ee_body_name!r}"
        )
        print(f"  ✓ grasp_backend: {spec.grasp_backend} → "
              f"{type(skel.grasp_backend).__name__}")
    finally:
        os.chdir(orig_cwd)

    vrep = ws / "validate_report.json"
    if vrep.exists():
        report = json.loads(vrep.read_text())
        for t in report.get("tests", []):
            print(f"  validate: {t['test']} → ok={t['ok']} "
                  f"metric={t.get('metric')} detail={t.get('detail','')[:80]}")

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
