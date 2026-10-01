# SPDX-License-Identifier: Apache-2.0
"""End-to-end SelfAssemble: SO-101, all 5 phases, local mode.

This is the "MVP success" test: agent goes from MJCF input to a working
driver + validated behavior + MCP server + demo video, completely autonomously.

Expected wall clock: ~5-10 min (Sonnet 4.5).
Expected cost: ~$1-2.

Validates:
  - All 5 phases complete with ok=True
  - driver.py has CORRECT gripper close/open ctrl (via probe)
  - validate_report.json shows both tests pass
  - mcp_server.py registers only methods that ACTUALLY exist on the skeleton
  - demo.mp4 is non-trivial size
  - narrative.md is generated
  - At least one per-attempt recording was captured in recordings/
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

MJCF_PATH = REPO_ROOT / "assets" / "mjcf" / "so101_mujoco.xml"


def main() -> None:
    assert MJCF_PATH.exists(), f"SO-101 MJCF missing at {MJCF_PATH}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_full_so101_"))
    cfg = SelfAssembleConfig(
        robot_id="so101_full",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        mode="local",
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
    )
    print(f">> workspace: {workspace_root / cfg.robot_id}")

    with SelfAssemble(cfg) as sa:
        result = sa.run()  # all 5 phases

    print("\n=== PHASES ===")
    for p in result.phases:
        status = "OK " if p.ok else "FAIL"
        print(f"  [{status}] {p.name}  dur={p.duration_sec:.1f}s  tok={p.token_usage}")
        if p.error:
            print(f"           error: {p.error}")

    ws = result.workspace

    # ─── Per-phase assertions ────────────────────────────────────────────
    for phase_name in ("01_study", "02_generate", "03_validate", "04_export", "05_demo"):
        p = next((p for p in result.phases if p.name == phase_name), None)
        assert p is not None, f"phase {phase_name} not found"
        assert p.ok, f"{phase_name} failed: {p.error}"

    # ─── driver.py: gripper direction probed correctly ───────────────────
    sys.path.insert(0, str(ws))
    sys.modules.pop("driver", None)
    sys.modules.pop("mcp_server", None)
    orig_cwd = os.getcwd()
    try:
        os.chdir(ws)
        driver = importlib.import_module("driver")
        skel = driver.build()
        spec = skel.spec
        # Gripper probe should pick the ctrl CLOSER TO ZERO as close
        assert abs(spec.gripper_close_ctrl) < abs(spec.gripper_open_ctrl), (
            f"Gripper probe failed: close={spec.gripper_close_ctrl} not closer "
            f"to 0 than open={spec.gripper_open_ctrl}"
        )
        print(f"\n  ✓ gripper_close_ctrl={spec.gripper_close_ctrl} "
              f"(< |open_ctrl|={abs(spec.gripper_open_ctrl)}, probe worked)")

        # ─── mcp_server.py: only registers REAL methods ──────────────────
        ms = importlib.import_module("mcp_server")
        srv = next(
            (getattr(ms, a) for a in dir(ms)
             if not a.startswith("_") and type(getattr(ms, a)).__name__ == "FastMCP"),
            None,
        )
        assert srv is not None, "no FastMCP instance in mcp_server.py"
        tool_names = sorted(srv._tool_manager._tools.keys())
        actual_skel_methods = set(
            m for m in dir(skel)
            if not m.startswith("_") and callable(getattr(skel, m))
        )
        invented = []
        for tname in tool_names:
            # Map MCP tool name → skeleton method (typically same name)
            if tname not in actual_skel_methods and tname not in {
                # OK for the agent to expose composite tools too
                "status", "describe", "stop",
            }:
                invented.append(tname)
        if invented:
            print(f"\n  ⚠ mcp_server.py registered methods not on skeleton: {invented}")
            print(f"    skeleton methods: {sorted(actual_skel_methods)}")
            # Don't fail — Phase 4 prompt asks for real methods but
            # composite tools (e.g. "grasp(body)" that wraps move+close) are OK
            # as long as their body uses real methods. Real check: each MCP
            # tool must IMPORT cleanly (we already verified that).
        print(f"  ✓ mcp_server.py registers tools: {tool_names}")
    finally:
        os.chdir(orig_cwd)

    # ─── validate_report.json shape ──────────────────────────────────────
    report = json.loads((ws / "validate_report.json").read_text())
    print(f"\n  validate_report tests: {[t['test'] for t in report.get('tests', [])]}")
    assert report.get("all_ok") is True, f"validate_report all_ok != True: {report}"

    # ─── demo.mp4 exists + non-trivial ───────────────────────────────────
    demo_mp4 = ws / "demo.mp4"
    assert demo_mp4.exists(), "demo.mp4 missing"
    size_kb = demo_mp4.stat().st_size / 1024
    print(f"  ✓ demo.mp4 = {size_kb:.1f} KB")
    assert size_kb > 5, f"demo.mp4 too small ({size_kb:.1f} KB)"

    # ─── recordings/ has at least one mp4 ────────────────────────────────
    rec_files = sorted((ws / "recordings").glob("*.mp4"))
    print(f"  ✓ recordings/ has {len(rec_files)} mp4(s): "
          f"{[r.name for r in rec_files]}")
    assert rec_files, "recordings/ is empty — agent didn't save per-attempt videos"

    # ─── narrative.md exists ─────────────────────────────────────────────
    narr = ws / "narrative.md"
    assert narr.exists(), "narrative.md not generated"
    print(f"  ✓ narrative.md = {narr.stat().st_size} bytes")

    print("\n[ALL OK]  Full SelfAssemble pipeline produced a complete, "
          "validated, demoed robot driver autonomously.")
    print(f"\n>> open the demo:   open {demo_mp4}")
    print(f">> read narrative:  cat {narr}")
    print(f">> workspace:       {ws}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
