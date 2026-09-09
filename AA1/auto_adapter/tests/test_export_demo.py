# SPDX-License-Identifier: Apache-2.0
"""Phase 4 EXPORT + Phase 5 DEMO smoke test (local mode).

Reuses the existing workspace produced by test_orchestrator_so101_partial.py
(STUDY+GENERATE already done) and Phase 3 VALIDATE result from
test_validate_only.py. Runs only EXPORT and DEMO.

Validates:
  - mcp_server.py is written and imports cleanly
  - mcp_server.py registers the expected tools (we inspect via FastMCP API)
  - demo.mp4 is written and is non-trivial in size (real video frames, not 0B)
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig


def _find_latest_workspace() -> Path:
    import glob
    cands = sorted(glob.glob("/var/folders/**/auto_adapter_orch_smoke_*", recursive=True))
    cands += sorted(glob.glob("/tmp/auto_adapter_orch_smoke_*"))
    for d in reversed(cands):
        if (Path(d) / "so101_smoke" / "driver.py").exists() and \
           (Path(d) / "so101_smoke" / "validate_report.json").exists():
            return Path(d)
    raise SystemExit(
        "No workspace with both driver.py + validate_report.json found. "
        "Run test_orchestrator_so101_partial.py then test_validate_only.py first."
    )


def main() -> None:
    workspace_root = _find_latest_workspace()
    ws = workspace_root / "so101_smoke"
    print(f">> reusing workspace: {ws}")

    # Re-symlink mjcf.xml in case it was deleted
    real_mjcf = REPO_ROOT / "assets" / "mjcf" / "so101_mujoco.xml"
    mjcf_link = ws / "mjcf.xml"
    if not (mjcf_link.exists() or mjcf_link.is_symlink()):
        mjcf_link.symlink_to(real_mjcf)

    # Discard old export/demo artifacts so we know the NEW run produced them
    for stale in ["mcp_server.py", "demo.py", "demo.mp4"]:
        p = ws / stale
        if p.exists():
            p.unlink()

    cfg = SelfAssembleConfig(
        robot_id="so101_smoke",
        mjcf_path=real_mjcf,
        workspace_root=workspace_root,
        mode="local",
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
        max_iters_export=10,
        max_iters_demo=18,
    )

    # ─── Phase 4 EXPORT ──────────────────────────────────────────────────
    with SelfAssemble(cfg) as sa:
        export_res = sa._phase_export()

    print("\n=== PHASE 4 EXPORT ===")
    print(f"  ok={export_res.ok} dur={export_res.duration_sec:.1f}s tokens={export_res.token_usage}")
    print(f"  final_text: {export_res.final_text[:300]!r}")
    print(f"  trace: {export_res.trace_path}")
    print(f"  artifacts: {[str(a) for a in export_res.artifact_paths]}")
    if export_res.error:
        print(f"  error: {export_res.error}")
    assert export_res.ok, f"EXPORT failed: {export_res.error}"

    mcp_path = ws / "mcp_server.py"
    assert mcp_path.exists(), "mcp_server.py not written"
    print(f"\n  mcp_server.py = {mcp_path} ({mcp_path.stat().st_size} bytes)")

    # Real import check — go beyond the agent's syntax check
    sys.path.insert(0, str(ws))
    sys.modules.pop("mcp_server", None)
    sys.modules.pop("driver", None)
    orig_cwd = os.getcwd()
    try:
        os.chdir(ws)
        ms = importlib.import_module("mcp_server")
        # Look for a FastMCP instance — agents typically name it `mcp` or `app`
        srv = next(
            (getattr(ms, a) for a in dir(ms)
             if not a.startswith("_") and type(getattr(ms, a)).__name__ == "FastMCP"),
            None,
        )
        assert srv is not None, "no FastMCP instance found in mcp_server.py"
        # FastMCP keeps tools in srv._tool_manager._tools (impl detail).
        # Fall back to introspecting attribute names if internals change.
        try:
            tool_names = sorted(srv._tool_manager._tools.keys())
        except AttributeError:
            tool_names = []
        print(f"  registered tools: {tool_names}")
        assert tool_names, "mcp_server.py registered zero tools"
        # We asked for at least these four in the prompt
        for required in ("home", "move_cartesian"):
            assert any(required in t for t in tool_names), \
                f"required tool {required!r} not in registered tools: {tool_names}"
    finally:
        os.chdir(orig_cwd)
    print("  ✓ mcp_server.py imports cleanly + registers tools")

    # ─── Phase 5 DEMO ────────────────────────────────────────────────────
    with SelfAssemble(cfg) as sa:
        demo_res = sa._phase_demo()

    print("\n=== PHASE 5 DEMO ===")
    print(f"  ok={demo_res.ok} dur={demo_res.duration_sec:.1f}s tokens={demo_res.token_usage}")
    print(f"  final_text: {demo_res.final_text[:400]!r}")
    print(f"  trace: {demo_res.trace_path}")
    print(f"  artifacts: {[str(a) for a in demo_res.artifact_paths]}")
    if demo_res.error:
        print(f"  error: {demo_res.error}")
    assert demo_res.ok, f"DEMO failed: {demo_res.error}"

    demo_mp4 = ws / "demo.mp4"
    assert demo_mp4.exists(), "demo.mp4 not written"
    size_kb = demo_mp4.stat().st_size / 1024
    print(f"\n  demo.mp4 = {demo_mp4} ({size_kb:.1f} KB)")
    assert size_kb > 5, f"demo.mp4 suspiciously small ({size_kb:.1f} KB) — probably empty"

    print(f"\n[ALL OK]  Phase 4 EXPORT + Phase 5 DEMO completed end-to-end.")
    print(f">> open the demo: open {demo_mp4}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
