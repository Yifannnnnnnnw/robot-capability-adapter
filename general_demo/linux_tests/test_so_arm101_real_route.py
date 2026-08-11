"""Explicit real Linux integration test. It never skips missing prerequisites."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from autoadapter2.integrations.so_arm101.readiness import CHECK_IDS, run_readiness


ROOT = Path(__file__).resolve().parents[1]


def test_real_lerobot_feetech_pty_mujoco_six_check_route(tmp_path: Path) -> None:
    assert sys.platform == "linux", "formal readiness must run on Linux"
    runtime_lock = Path(os.environ["AUTOADAPTER_RUNTIME_LOCK"])
    model = Path(os.environ["AUTOADAPTER_SO101_MODEL"])
    direction = os.environ["AUTOADAPTER_GRIPPER_DIRECTION"]
    assert direction in {"tick-increases-qpos", "tick-decreases-qpos"}
    # The report and its evidence must share one content-addressed artifact root
    # with the pinned manifest/profile files.  Pytest's /tmp directory is outside
    # that root, so use its unique basename under the disposable container copy.
    reference_root = ROOT.parent
    report_path = reference_root / "run_artifacts" / tmp_path.name / "readiness_report.json"
    report = run_readiness(
        run_id="linux-real-so101-route",
        manifest_path=ROOT / "integrations/so-arm101/integration_manifest.json",
        profile_path=ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json",
        runtime_lock_path=runtime_lock,
        model_path=model,
        gripper_tick_increases_qpos=direction == "tick-increases-qpos",
        report_path=report_path,
        reference_root=reference_root,
    )
    assert [item["check_id"] for item in report["checks"]] == list(CHECK_IDS)
    assert all(item["verdict"] == "PASS" for item in report["checks"]), report
    assert report["cleanup"]["verdict"] == "PASS", report
    assert report["verdict"] == "PASS", report
