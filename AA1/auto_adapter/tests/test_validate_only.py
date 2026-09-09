# SPDX-License-Identifier: Apache-2.0
"""Phase 3 VALIDATE smoke test (local mode) on an EXISTING workspace.

Reuses the workspace produced by test_orchestrator_so101_partial.py — runs
just Phase 3 against the agent-generated driver.py to see if a real grasp
test catches the inverted gripper_close_ctrl bug.

Pass the workspace path as argv[1], or rely on the most-recent
auto_adapter_orch_smoke_* directory under /var/folders/.../T/.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig


def _find_latest_workspace() -> Path:
    import glob
    cands = sorted(glob.glob("/var/folders/**/auto_adapter_orch_smoke_*", recursive=True))
    cands += sorted(glob.glob("/tmp/auto_adapter_orch_smoke_*"))
    if not cands:
        raise SystemExit(
            "No auto_adapter_orch_smoke_* workspace found. Run "
            "test_orchestrator_so101_partial.py first."
        )
    # Pick the most-recent one with a driver.py in it
    for d in reversed(cands):
        if (Path(d) / "so101_smoke" / "driver.py").exists():
            return Path(d)
    raise SystemExit("No workspace contains a driver.py — STUDY/GENERATE never completed.")


def main() -> None:
    if len(sys.argv) > 1:
        workspace_root = Path(sys.argv[1])
    else:
        workspace_root = _find_latest_workspace()

    ws = workspace_root / "so101_smoke"
    assert (ws / "driver.py").exists(), f"missing driver.py at {ws}"
    assert (ws / "study.json").exists(), f"missing study.json at {ws}"
    print(f">> reusing workspace: {ws}")

    # Stash and re-symlink mjcf.xml: prior runs that pre-dated the symlink fix
    # may have copied the file instead.
    mjcf_link = ws / "mjcf.xml"
    real_mjcf = REPO_ROOT / "assets" / "mjcf" / "so101_mujoco.xml"
    if mjcf_link.exists() or mjcf_link.is_symlink():
        mjcf_link.unlink()
    mjcf_link.symlink_to(real_mjcf)

    # Throw away any previous validate_report.json so we know the new run wrote it
    for stale in ["validate_report.json", "validate.py"]:
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
        max_iters_validate=18,
    )

    with SelfAssemble(cfg) as sa:
        result = sa._phase_validate()

    print("\n=== PHASE 3 VALIDATE ===")
    print(f"  ok={result.ok} dur={result.duration_sec:.1f}s tokens={result.token_usage}")
    print(f"  final_text: {result.final_text[:400]!r}")
    print(f"  trace: {result.trace_path}")
    print(f"  artifacts: {[str(a) for a in result.artifact_paths]}")
    if result.error:
        print(f"  error: {result.error}")

    report_path = ws / "validate_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        print("\n=== validate_report.json ===")
        print(json.dumps(report, indent=2))
        # Check if grasp_lift caught the inverted gripper
        for t in report.get("tests", []):
            name = t.get("test")
            ok = t.get("ok")
            metric = t.get("metric")
            print(f"  -> {name}: ok={ok} metric={metric}")
            if name == "grasp_lift" and not ok:
                print(
                    f"\n  ✅ EXPECTED: grasp_lift FAILED (metric={metric}) — "
                    "this is the inverted-gripper bug being caught by VALIDATE, "
                    "as designed."
                )
    else:
        print("\n  !! validate_report.json not written")
        sys.exit(1)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
