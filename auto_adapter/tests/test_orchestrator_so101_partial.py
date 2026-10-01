# SPDX-License-Identifier: Apache-2.0
"""Partial-pipeline smoke test: run SelfAssemble through STUDY + GENERATE on SO-101.

This is the heaviest test we have so far. It exercises:
  * AgentCore CodeInterpreter session lifecycle (start + stop)
  * Phase 1 STUDY end-to-end (read_file + execute_python + write_file)
  * Phase 2 GENERATE end-to-end (list_skeletons + inspect_skeleton + …)
  * Cross-phase artifact handoff (study.json written by P1, read by P2)
  * The actual generated driver.py loading via SkeletonBase.from_mjcf

DGX phases (validate/export/demo) are skipped via `stop_after="generate"`,
so this test runs even when DGX ssh is down.

Costs: ~10–30k input tokens + one CI session for ~30–60s (a few cents total).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MJCF_PATH = REPO_ROOT / "assets" / "mjcf" / "so101_mujoco.xml"

from auto_adapter import SelfAssemble, SelfAssembleConfig


def _load_module_from_path(modname: str, path: Path):
    spec = importlib.util.spec_from_file_location(modname, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    assert MJCF_PATH.exists(), f"SO-101 MJCF missing at {MJCF_PATH}"

    workspace_root = Path(tempfile.mkdtemp(prefix="auto_adapter_orch_smoke_"))
    cfg = SelfAssembleConfig(
        robot_id="so101_smoke",
        mjcf_path=MJCF_PATH,
        workspace_root=workspace_root,
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        # Sonnet 4.6 is intermittently 503-throttled on this account; 4.5 is the
        # stable fallback for CI / smoke tests. Override via VECTOR_MODEL env.
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
        max_iters_study=12,
        max_iters_generate=16,
    )

    print(f">> workspace: {workspace_root / cfg.robot_id}")

    try:
        with SelfAssemble(cfg) as sa:
            result = sa.run(stop_after="generate")

        print("\n=== PHASES ===")
        for p in result.phases:
            status = "OK " if p.ok else "FAIL"
            print(f"  [{status}] {p.name}  dur={p.duration_sec:.1f}s  "
                  f"tok={p.token_usage}  err={p.error}")

        # ── Assertions ────────────────────────────────────────────────────
        ws = result.workspace
        # Run phases get the "NN_<name>" prefix; skipped phases keep the
        # short name — look for either form.
        def _find(*candidates: str):
            for c in candidates:
                for p in result.phases:
                    if p.name == c:
                        return p
            raise AssertionError(f"none of {candidates} in {[p.name for p in result.phases]}")

        p_study = _find("01_study", "study")
        p_gen = _find("02_generate", "generate")
        assert p_study.ok, f"STUDY failed: {p_study.error}\nfinal_text: {p_study.final_text}"
        assert p_gen.ok, f"GENERATE failed: {p_gen.error}\nfinal_text: {p_gen.final_text}"

        # study.json must parse and have the right shape
        study_path = ws / "study.json"
        study = json.loads(study_path.read_text())
        print(f"\n  study.estimated_class = {study.get('estimated_class')!r}")
        print(f"  study.dof = {study.get('dof')}")
        print(f"  study.ee_site = {study.get('ee_site')!r}")
        assert study.get("estimated_class") == "arm", f"expected arm, got {study.get('estimated_class')!r}"
        assert isinstance(study.get("dof"), int) and study["dof"] >= 5
        assert isinstance(study.get("joints"), list) and len(study["joints"]) >= 5

        # driver.py must import + call build() to produce a working skeleton
        driver_path = ws / "driver.py"
        assert driver_path.exists(), "driver.py missing"
        print(f"\n  driver.py = {driver_path}  ({driver_path.stat().st_size} bytes)")

        # Side-load and exercise it like the runtime would.
        # Repo root and workspace need to be on sys.path: workspace so
        # `import driver` works; repo root so `from auto_adapter.skeletons …`.
        sys.path.insert(0, str(REPO_ROOT))
        sys.path.insert(0, str(ws))
        sys.modules.pop("driver", None)
        # The agent generated a driver.py that passes mjcf_path="mjcf.xml" (workspace
        # relative — correct for the eventual DGX runtime). Run from the workspace
        # cwd so the relative path resolves.
        orig_cwd = os.getcwd()
        try:
            os.chdir(ws)
            driver = _load_module_from_path("driver_so101_smoke", driver_path)
            assert hasattr(driver, "build"), "driver.py missing build()"
            skel = driver.build()
            try:
                skel.home()
                desc = skel.describe()
                print(f"  skel.describe() = {desc}")
            except Exception as e:
                raise AssertionError(
                    f"driver.build().home() failed: {type(e).__name__}: {e}"
                ) from e
        finally:
            os.chdir(orig_cwd)

        # Soft check: warn if the gripper close/open ctrl looks inverted.
        # (Phase 3 VALIDATE will catch this for real via an actual grasp test.)
        try:
            spec = skel.spec
            if abs(spec.gripper_close_ctrl) > abs(spec.gripper_open_ctrl):
                print(
                    f"\n  !! WARNING: gripper_close_ctrl={spec.gripper_close_ctrl} "
                    f"is larger in magnitude than gripper_open_ctrl={spec.gripper_open_ctrl}. "
                    "The agent may have inverted the close/open convention; "
                    "Phase 3 VALIDATE would normally catch this via a real grasp test."
                )
        except Exception:
            pass

        print("\n[ALL OK]  STUDY + GENERATE pipeline produced a working driver.py")
        sys.stdout.flush()
    finally:
        # Keep workspace on disk for post-mortem if anything went wrong.
        print(f"\n>> workspace preserved at: {workspace_root}")
    os._exit(0)


if __name__ == "__main__":
    main()
